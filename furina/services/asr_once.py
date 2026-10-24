"""听一条语音：用 GPT-SoVITS 整合包自带的 FunASR（paraformer 中文 + VAD + 标点）把一个音频文件转成文字。

由 voice_asr.py 用整合包的 runtime/python.exe 调起，工作目录 = 整合包目录；
只读本地模型（tools/asr/models），不联网下载；结果以一行 JSON 打到标准输出：{"text": "..."}。
"""
import json
import os
import sys


def main() -> int:
    if len(sys.argv) < 2 or not os.path.isfile(sys.argv[1]):
        print(json.dumps({"text": "", "error": "no file"}, ensure_ascii=False))
        return 2
    os.environ.setdefault("MODELSCOPE_OFFLINE", "1")
    from funasr import AutoModel

    base = os.path.join("tools", "asr", "models")
    model = AutoModel(
        model=os.path.join(base, "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"),
        vad_model=os.path.join(base, "speech_fsmn_vad_zh-cn-16k-common-pytorch"),
        punc_model=os.path.join(base, "punc_ct-transformer_zh-cn-common-vocab272727-pytorch"),
        device="cpu", disable_update=True, disable_pbar=True, log_level="ERROR",
    )
    res = model.generate(input=sys.argv[1])
    text = (res[0].get("text") if res else "") or ""
    sys.stdout.write("\n" + json.dumps({"text": text.strip()}, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
