#!/usr/bin/env bash
# Start-up for the transcription job when it runs from a stock PyTorch image
# instead of our own (see README.md, "Without a built image"). Nebius injects
# this script, requirements.txt and src/ into the container; this installs
# what the Dockerfile would have baked in, then runs the job.
#
# Every way the job can end leaves a report. The job writes its own when it
# finishes or fails in a way it handles (exit 0, or 3). If installing fails, or
# the job crashes outright, a stdlib-only fallback reports the stage and the
# tail of the output: package names, errors and tracebacks, never anything
# that was said (the job never prints transcript text).
set -uo pipefail

APP=/opt/hearally
LOG=/tmp/install.log
RUNLOG=/tmp/run.log
export PYTHONPATH="$APP/src"
export PYTHONUNBUFFERED=1
export HF_HOME=/tmp/hf

fail() {
  python -c "from hearally_transcriber.main import report_failure; report_failure('$1', '$2')" || true
  exit 1
}

echo '{"stage":"install-system"}'
{
  apt-get update -qq &&
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      ffmpeg libsndfile1 build-essential
} >"$LOG" 2>&1 || fail install-system "$LOG"

echo '{"stage":"install-python"}'
python --version >"$LOG" 2>&1
# The image's Python is Ubuntu's, which refuses installs by default (PEP 668).
# This container is disposable and single-purpose, so install into it
# directly, keeping the image's CUDA-matched PyTorch.
python -m pip install --no-cache-dir --break-system-packages \
  -r "$APP/requirements.txt" >>"$LOG" 2>&1 || fail install-python "$LOG"

echo '{"stage":"transcribe"}'
python -m hearally_transcriber.main 2>"$RUNLOG"
code=$?
tail -c 2000 "$RUNLOG" >&2
if [ "$code" -ne 0 ] && [ "$code" -ne 3 ]; then
  fail "crashed-$code" "$RUNLOG"
fi
exit "$code"
