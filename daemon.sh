dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd $dir

if [ ! -d ./venv ]; then
  echo "Run ./install.sh first"
  exit 1
fi

set -o allexport
source .env
set +o allexport


./venv/bin/python3 ./src/daemon.py $@

