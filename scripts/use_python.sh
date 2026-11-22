# Sourced by loop.sh and verify.sh after loop/config.env. Finds the Python of the conda environment and puts that
# environment first on PATH, for the script and for everything it starts: loop_main.py, the three agents and the
# commands they run ("python scripts/try_question.py", pytest). A shell opened by Claude Code or another tool has no
# environment activated, and on Windows a bare "python" is then missing or is the Microsoft Store stub.
#   CONDA_ENV      the environment's name (default kosis). Empty: no environment, the python on PATH as it is
#   CONDA_ENV_DIR  the environment's folder, for when it is not found by its name ("conda env list" shows it)
#   PYTHON         a python to use as it is (full path); its folder then goes first on PATH
# use_python leaves PYTHON set to the python to run, or says what is missing and returns 1.

to_posix() { if command -v cygpath >/dev/null 2>&1; then cygpath -u "$1"; else printf '%s\n' "$1"; fi; }
to_mixed() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s\n' "$1"; fi; }
to_windows() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s\n' "$1"; fi; }

python_in() {   # the python of an environment folder; nothing when the folder holds none
    if [ -f "$1/python.exe" ]; then printf '%s\n' "$1/python.exe"
    elif [ -x "$1/bin/python" ]; then printf '%s\n' "$1/bin/python"
    fi
}

env_folders() {   # where an environment called $1 may be, one folder a line, the likeliest first
    local name="$1" home root line
    if [ -n "${CONDA_PREFIX:-}" ]; then   # an activated environment: this one, or the base one that holds it
        root="$(to_posix "$CONDA_PREFIX")"
        [ "$(basename "$root")" = "$name" ] && printf '%s\n' "$root"
        printf '%s\n' "$root/envs/$name"
    fi
    if [ -n "${CONDA_EXE:-}" ]; then      # <root>/Scripts/conda.exe or <root>/bin/conda
        root="$(to_posix "$CONDA_EXE")"
        printf '%s\n' "$(dirname "$(dirname "$root")")/envs/$name"
    fi
    for home in "${HOME:-}" "${USERPROFILE:+$(to_posix "$USERPROFILE")}"; do
        [ -n "$home" ] || continue
        if [ -f "$home/.conda/environments.txt" ]; then   # conda's own list of every environment it made
            while IFS= read -r line || [ -n "$line" ]; do
                line="${line%$'\r'}"
                [ -n "$line" ] || continue
                line="$(to_posix "$line")"
                [ "$(basename "$line")" = "$name" ] && printf '%s\n' "$line"
            done < "$home/.conda/environments.txt"
        fi
        printf '%s\n' "$home/.conda/envs/$name"
        for root in anaconda3 miniconda3 miniforge3 mambaforge AppData/Local/anaconda3 AppData/Local/miniconda3 \
                    AppData/Local/miniforge3; do
            printf '%s\n' "$home/$root/envs/$name"
        done
    done
    for home in "${LOCALAPPDATA:+$(to_posix "$LOCALAPPDATA")}" "${PROGRAMDATA:+$(to_posix "$PROGRAMDATA")}" /opt; do
        [ -n "$home" ] || continue
        for root in anaconda3 miniconda3 miniforge3 conda; do printf '%s\n' "$home/$root/envs/$name"; done
    done
}

use_python() {
    local name="${CONDA_ENV-kosis}" dir="" found="" folder
    if [ -n "${LOOP_PYTHON:-}" ]; then   # set by the script that started this one: PATH is already in order
        export PYTHON="$LOOP_PYTHON"
        return 0
    fi
    if [ -n "${PYTHON:-}" ]; then
        found="$(to_posix "$PYTHON")"
        if [ ! -f "$found" ] && command -v "$PYTHON" >/dev/null 2>&1; then   # a command's name, found on PATH
            export LOOP_PYTHON="$PYTHON"
            return 0
        fi
        if [ ! -f "$found" ]; then
            echo "- PYTHON에 적은 파일이 없음: $PYTHON (loop/config.env. 비우면 conda 환경 ${name:-없음}에서 찾음)" >&2
            return 1
        fi
        dir="$(dirname "$found")"
        if [ "$(basename "$dir")" = bin ]; then dir="$(dirname "$dir")"; fi
    elif [ -n "${CONDA_ENV_DIR:-}" ]; then
        dir="$(to_posix "$CONDA_ENV_DIR")"
        found="$(python_in "$dir")"
        if [ -z "$found" ]; then
            echo "- CONDA_ENV_DIR에 python이 없음: $CONDA_ENV_DIR (loop/config.env. conda env list에 나오는 폴더를 적을 것)" >&2
            return 1
        fi
    elif [ -n "$name" ]; then
        while IFS= read -r folder; do
            found="$(python_in "$folder")"
            if [ -n "$found" ]; then dir="$folder"; break; fi
        done < <(env_folders "$name")
        if [ -z "$found" ]; then
            echo "- conda 환경 $name 을 찾지 못함: loop/config.env의 CONDA_ENV_DIR에 그 환경의 폴더를 적을 것" \
                 "(Anaconda Prompt의 conda env list에 나옴. 예: CONDA_ENV_DIR=\"C:/Users/<사용자>/anaconda3/envs/$name\")" >&2
            return 1
        fi
    else
        if ! python -c "" >/dev/null 2>&1; then
            echo "- PATH에서 python을 실행하지 못함: loop/config.env의 CONDA_ENV, CONDA_ENV_DIR, PYTHON 가운데 하나를 적을 것" >&2
            return 1
        fi
        export PYTHON=python LOOP_PYTHON=python
        return 0
    fi
    if [ -f "$dir/python.exe" ]; then   # Windows: what "conda activate" puts on PATH (Library/bin holds the DLLs)
        PATH="$dir:$dir/Library/mingw-w64/bin:$dir/Library/usr/bin:$dir/Library/bin:$dir/Scripts:$dir/bin:$PATH"
    else
        PATH="$dir/bin:$PATH"
    fi
    PYTHON="$(to_mixed "$found")"
    LOOP_PYTHON="$PYTHON"
    export PATH PYTHON LOOP_PYTHON
    if [ -d "$dir/conda-meta" ]; then
        CONDA_PREFIX="$(to_windows "$dir")"
        CONDA_DEFAULT_ENV="$(basename "$dir")"
        export CONDA_PREFIX CONDA_DEFAULT_ENV
    fi
}
