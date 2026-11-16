"""`python -m codeagent.eval [questions.yaml]`"""
import logging
import sys

from .runner import run_eval

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s eval %(levelname)s %(message)s")
    run = run_eval(sys.argv[1] if len(sys.argv) > 1 else None)
    sys.exit(0 if run["summary"]["questions"] else 1)
