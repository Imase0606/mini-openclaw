FROM class-agent-base:latest

WORKDIR /app
COPY . /app

ENV FASTER_WHISPER_MODEL_PATH=/app/models/faster-whisper-base
ENV BILIBILI_AUTH_MODE=ephemeral

# requirements.txt installs the project and its runtime dependencies, which
# creates the console script. The ASR model is bundled in the upload because
# neither build-time nor runtime Hub access is reliable on the course platform.
RUN pip install --no-cache-dir \
        -i https://mirrors.aliyun.com/pypi/simple/ \
        -r requirements.txt \
    && python -c "import os, pathlib; p=pathlib.Path(os.environ['FASTER_WHISPER_MODEL_PATH']); required=('model.bin','config.json','tokenizer.json','vocabulary.txt'); missing=[name for name in required if not (p/name).is_file()]; assert not missing, f'missing bundled ASR files: {missing}'; assert (p/'model.bin').stat().st_size > 100_000_000, 'bundled ASR model is incomplete'" \
    && python -c "import shutil; assert shutil.which('mini-openclaw'), 'mini-openclaw console script was not installed'"

# Keep the base image entrypoint/CMD used by the course platform.
