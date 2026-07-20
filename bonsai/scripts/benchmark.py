#!/usr/bin/env python3
"""デプロイ済み Lambda(Bonsai / PrismML) の性能・品質ベンチマーク。

計測項目:
  - tokens/sec     : Lambda 内で生成時間と completion_tokens から算出（サーバー側計測＝正確）
  - コールドスタート : コンテナ強制リセット後の初回 invoke の総時間（+モデルロード/生成内訳）
  - 体感品質        : 各出力を Claude(LLM-as-judge) が 1〜5 で採点（ANTHROPIC_API_KEY 必要）

5回(既定)のテストプロンプトを warm で回して tokens/sec と品質を集計し、
コールドスタートは別途1回計測する。結果は標準出力とログファイルへ出力する。

環境変数:
  FUNCTION_NAME   (既定 ai-chat-bonsai-dev-slack)
  AWS_REGION      (既定 us-east-1)
  AWS_PROFILE     (任意)
  RUNS            (既定 5)         warm 実行回数 / 使用プロンプト数
  JUDGE           (既定 1)         0 で品質採点をスキップ
  JUDGE_BACKEND   (既定 auto)      auto / sdk(APIキー) / cli(claude -p) / off
  JUDGE_MODEL     (既定 claude-opus-4-8)
  COLD_EACH       (既定 0)         1 で毎回コールドを強制（5回コールド計測・低速）
  BENCH_LOG       (既定 benchmark.log)
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

import boto3
from botocore.config import Config

FUNCTION_NAME = os.environ.get("FUNCTION_NAME", "ai-chat-bonsai-dev-slack")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
AWS_PROFILE = os.environ.get("AWS_PROFILE")
RUNS = int(os.environ.get("RUNS", "5"))
JUDGE = os.environ.get("JUDGE", "1") != "0"
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "claude-opus-4-8")
COLD_EACH = os.environ.get("COLD_EACH", "0") == "1"
BENCH_LOG = os.environ.get("BENCH_LOG", "benchmark.log")
BENCH_CSV = os.environ.get("BENCH_CSV", "benchmark.csv")
MODEL_LABEL = os.environ.get("MODEL_LABEL", "")  # スプレッドシート用のモデル表示名（空なら応答のmodel名）
MAX_TOKENS = int(os.environ.get("BENCH_MAX_TOKENS", "256"))

# 多様な観点（事実 / 計算 / 説明 / 書き換え / 技術）でプロンプトを用意。
PROMPTS = [
    "日本の首都はどこですか？理由も添えて100文字以内で答えてください。",
    "3 + 5 × 2 はいくつですか？計算過程も示してください。",
    "犬と猫の違いを2点、簡潔に説明してください。",
    "次の文を丁寧語に直してください：「メシ食った？」",
    "再帰関数とは何か、初心者向けに1文で説明してください。",
]

JUDGE_SYSTEM = (
    "あなたは厳格な評価者です。与えられた質問に対する AI の回答の品質を 1〜5 の整数で採点してください"
    "（5=非常に良い, 4=良い, 3=普通, 2=やや不十分, 1=不適切/誤り）。"
    "正確さ・有用性・一貫性・指示への追従・日本語の自然さを総合評価し、短い理由を日本語で添えてください。"
)
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["score", "reason"],
    "additionalProperties": False,
}


def make_lambda_client():
    session = boto3.Session(profile_name=AWS_PROFILE) if AWS_PROFILE else boto3.Session()
    # コールド/重いモデルに備え読取りタイムアウトは長め（Lambda timeout より大きく）。SDK リトライは無効。
    read_to = int(os.environ.get("BENCH_READ_TIMEOUT", "310"))
    cfg = Config(read_timeout=read_to, connect_timeout=10, retries={"max_attempts": 0})
    return session.client("lambda", region_name=AWS_REGION, config=cfg)


def force_cold(lc) -> None:
    """description を更新してコンテナを強制リセット（次回 invoke をコールドにする）。"""
    stamp = f"benchmark-cold-{int(time.time())}"
    lc.update_function_configuration(FunctionName=FUNCTION_NAME, Description=stamp)
    lc.get_waiter("function_updated_v2").wait(FunctionName=FUNCTION_NAME)


def invoke(lc, prompt: str) -> dict:
    """1回 invoke し、結果(辞書)と client 側の総時間(latency)を返す。"""
    payload = json.dumps({"prompt": prompt, "max_tokens": MAX_TOKENS}).encode("utf-8")
    out = {"latency": None, "error": None, "output": None, "model": None,
           "tokens_per_sec": None, "gen_sec": None, "model_load_sec": None}
    t0 = time.time()
    try:
        resp = lc.invoke(FunctionName=FUNCTION_NAME, Payload=payload)
        raw = resp["Payload"].read().decode("utf-8")
    except Exception as e:  # noqa: BLE001 - 読取りタイムアウト等で全体を落とさず記録して続行
        out["latency"] = round(time.time() - t0, 2)
        out["error"] = f"invoke error: {type(e).__name__}: {str(e)[:200]}"
        return out
    out["latency"] = round(time.time() - t0, 2)
    if resp.get("FunctionError"):
        out["error"] = raw[:500]
        return out
    try:
        body = json.loads(json.loads(raw)["body"])
        out["output"] = body.get("output")
        out["model"] = body.get("model")
        out["tokens_per_sec"] = body.get("tokens_per_sec")
        out["gen_sec"] = body.get("gen_sec")
        out["model_load_sec"] = body.get("model_load_sec")
    except (KeyError, json.JSONDecodeError) as e:
        out["error"] = f"parse error: {e}: {raw[:300]}"
    return out


_USER_TMPL = (
    "# 質問\n{prompt}\n\n# AIの回答\n{output}\n\n"
    '上記の回答を採点し、JSON {{"score": 1〜5の整数, "reason": "短い理由"}} のみで答えてください。'
)


def _extract_json(text: str) -> dict:
    """テキストから最初の { 〜 最後の } を取り出して score/reason を返す。"""
    s, e = text.find("{"), text.rfind("}")
    data = json.loads(text[s:e + 1] if s >= 0 and e > s else text)
    return {"score": data.get("score"), "reason": data.get("reason")}


def _make_sdk_judge():
    """ANTHROPIC_API_KEY を使う Anthropic SDK バックエンド。"""
    import anthropic

    client = anthropic.Anthropic()

    def judge(prompt: str, output: str) -> dict:
        content = _USER_TMPL.format(prompt=prompt, output=output)
        try:
            try:
                msg = client.messages.create(
                    model=JUDGE_MODEL, max_tokens=512, system=JUDGE_SYSTEM,
                    messages=[{"role": "user", "content": content}],
                    output_config={"format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
                )
            except TypeError:  # 古い SDK は output_config 非対応
                msg = client.messages.create(
                    model=JUDGE_MODEL, max_tokens=512, system=JUDGE_SYSTEM,
                    messages=[{"role": "user", "content": content}],
                )
            return _extract_json("".join(b.text for b in msg.content if b.type == "text"))
        except Exception as e:  # noqa: BLE001
            return {"score": None, "reason": f"judge error: {e}"}

    return judge


def _make_cli_judge():
    """Claude Code CLI(`claude -p`) バックエンド。APIキー不要、既存の Claude 認証を使う。"""
    def judge(prompt: str, output: str) -> dict:
        content = JUDGE_SYSTEM + "\n\n" + _USER_TMPL.format(prompt=prompt, output=output)
        try:
            r = subprocess.run(
                ["claude", "-p", content, "--model", JUDGE_MODEL],
                capture_output=True, text=True, timeout=150,
            )
            if r.returncode != 0:
                return {"score": None, "reason": f"claude -p error: {r.stderr.strip()[:200]}"}
            return _extract_json(r.stdout)
        except Exception as e:  # noqa: BLE001
            return {"score": None, "reason": f"judge error: {e}"}

    return judge


def make_judge():
    """(judge関数, ラベル) を返す。利用不可なら (None, 'off')。"""
    backend = os.environ.get("JUDGE_BACKEND", "auto")
    if not JUDGE or backend == "off":
        return None, "off"

    have_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    try:
        import anthropic  # noqa: F401
        have_sdk = True
    except ImportError:
        have_sdk = False
    have_cli = shutil.which("claude") is not None

    if backend == "auto":
        if have_key and have_sdk:
            backend = "sdk"
        elif have_cli:
            backend = "cli"
        else:
            print("[warn] judge 不可（APIキー+SDK も claude CLI も無い）→ 採点スキップ", file=sys.stderr)
            return None, "off"

    if backend == "sdk":
        if not (have_key and have_sdk):
            print("[warn] sdk バックエンド不可（ANTHROPIC_API_KEY/anthropic 不足）→ スキップ", file=sys.stderr)
            return None, "off"
        return _make_sdk_judge(), f"sdk:{JUDGE_MODEL}"

    if backend == "cli":
        if not have_cli:
            print("[warn] cli バックエンド不可（claude コマンド無し）→ スキップ", file=sys.stderr)
            return None, "off"
        return _make_cli_judge(), f"cli:{JUDGE_MODEL}"

    print(f"[warn] 未知の JUDGE_BACKEND={backend} → スキップ", file=sys.stderr)
    return None, "off"


def main() -> int:
    lc = make_lambda_client()
    judge, judge_label = make_judge()
    prompts = PROMPTS[:RUNS] if RUNS <= len(PROMPTS) else PROMPTS * (RUNS // len(PROMPTS) + 1)
    prompts = prompts[:RUNS]

    lines: list[str] = []

    def emit(s: str = "") -> None:
        print(s)
        lines.append(s)

    started = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    emit("=" * 72)
    emit(f"LLM on Lambda ベンチマーク  ({started})")
    emit(f"  function={FUNCTION_NAME}  region={AWS_REGION}  runs={RUNS}  max_tokens={MAX_TOKENS}")
    emit(f"  judge={judge_label}  cold_each={COLD_EACH}")
    emit("=" * 72)

    # ---- コールドスタート計測（COLD_EACH でなければ1回） ----
    cold_samples: list[dict] = []
    if not COLD_EACH:
        emit("\n[コールドスタート] コンテナ強制リセット → 初回 invoke 計測中 ...")
        force_cold(lc)
        r = invoke(lc, prompts[0])
        cold_samples.append(r)
        if r["error"]:
            emit(f"  ✖ エラー: {r['error']}")
        else:
            emit(f"  総時間={r['latency']}s  モデルロード={r['model_load_sec']}s  生成={r['gen_sec']}s")

    # ---- warm 実行（tokens/sec & 品質） ----
    emit(f"\n[テスト実行] {RUNS} 回")
    results: list[dict] = []
    for i, prompt in enumerate(prompts, 1):
        if COLD_EACH:
            force_cold(lc)
        r = invoke(lc, prompt)
        if r["error"]:
            emit(f"  run{i}: ✖ {r['error']}")
            results.append({"prompt": prompt, **r, "score": None, "reason": None})
            continue
        score, reason = None, None
        if judge and r["output"]:
            j = judge(prompt, r["output"])
            score, reason = j["score"], j["reason"]
        results.append({"prompt": prompt, **r, "score": score, "reason": reason})
        tps = r["tokens_per_sec"]
        cold_tag = f"  cold_total={r['latency']}s" if COLD_EACH else ""
        emit(f"  run{i}: tokens/sec={tps}  生成={r['gen_sec']}s  品質={score}{cold_tag}")

    # ---- 集計 ----
    tps_vals = [r["tokens_per_sec"] for r in results if r["tokens_per_sec"] is not None]
    score_vals = [r["score"] for r in results if isinstance(r["score"], int)]
    cold_vals = [r["latency"] for r in (cold_samples if not COLD_EACH else results) if not r["error"]]

    def avg(xs):
        return round(sum(xs) / len(xs), 2) if xs else None

    emit("\n" + "-" * 72)
    emit("【サマリ】")
    emit(f"  tokens/sec       : 平均 {avg(tps_vals)}  (min {min(tps_vals) if tps_vals else None} / max {max(tps_vals) if tps_vals else None}, n={len(tps_vals)})")
    if not COLD_EACH:
        emit(f"  コールドスタート : {cold_vals[0] if cold_vals else 'N/A'}s  (1サンプル)")
    else:
        emit(f"  コールドスタート : 平均 {avg(cold_vals)}s  (min {min(cold_vals) if cold_vals else None} / max {max(cold_vals) if cold_vals else None}, n={len(cold_vals)})")
    if judge:
        emit(f"  体感品質(1-5)    : 平均 {avg(score_vals)}  (n={len(score_vals)})")
    else:
        emit("  体感品質(1-5)    : (採点オフ)")
    emit("-" * 72)

    # ---- 全出力（目視確認用） ----
    emit("\n【各回の詳細（出力全文）】")
    for i, r in enumerate(results, 1):
        emit(f"\n--- run{i} ---")
        emit(f"Q: {r['prompt']}")
        if r["error"]:
            emit(f"ERROR: {r['error']}")
            continue
        emit(f"A: {r['output']}")
        emit(f"tokens/sec={r['tokens_per_sec']}  生成={r['gen_sec']}s  品質={r['score']}")
        if r["reason"]:
            emit(f"採点理由: {r['reason']}")

    # ---- CSV 追記出力（スプレッドシート貼り付け用・実行ごとに各run 1行を追記） ----
    cold_s = cold_vals[0] if (not COLD_EACH and cold_vals) else None
    model_name = MODEL_LABEL or next((r["model"] for r in results if r.get("model")), "")
    csv_header = [
        "timestamp", "model", "function", "run", "prompt",
        "tokens_per_sec", "gen_sec", "model_load_sec", "latency_s",
        "cold_start_s", "quality_score", "quality_reason", "output",
    ]
    try:
        new_file = (not os.path.exists(BENCH_CSV)) or os.path.getsize(BENCH_CSV) == 0
        with open(BENCH_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(csv_header)
            for i, r in enumerate(results, 1):
                w.writerow([
                    started, model_name, FUNCTION_NAME, i, r["prompt"],
                    r["tokens_per_sec"], r["gen_sec"], r["model_load_sec"], r["latency"],
                    (r["latency"] if COLD_EACH else cold_s), r["score"], r["reason"], r["output"],
                ])
        emit(f"\n[csv] {BENCH_CSV} に {len(results)} 行を追記しました")
    except OSError as e:
        print(f"[warn] CSV 追記に失敗: {e}", file=sys.stderr)

    # ---- ログファイル出力 ----
    try:
        with open(BENCH_LOG, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"[log] 全文を {BENCH_LOG} に保存しました（上書き）")
    except OSError as e:
        print(f"[warn] ログ保存に失敗: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
