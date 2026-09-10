"""Install a built wheel in an isolated venv, then exercise CLI outside checkout."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import venv


def main():
    wheel = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="aichestra-wheel-") as directory:
        root = Path(directory)
        env_dir = root / "venv"
        venv.EnvBuilder(with_pip=True).create(env_dir)
        bin_dir = env_dir / ("Scripts" if os.name == "nt" else "bin")
        python = bin_dir / ("python.exe" if os.name == "nt" else "python")
        cli = bin_dir / ("aichestra.exe" if os.name == "nt" else "aichestra")
        project = root / "unrelated project"
        project.mkdir()
        env = dict(os.environ)
        for key in ("PYTHONPATH", "AICHESTRA_REPO_ROOT", "ORCA_WORKER_TERMINAL_HANDLE"):
            env.pop(key, None)
        env.update(AICHESTRA_CONFIG_HOME=str(root / "config"), AICHESTRA_FAKE_PROVIDERS="1", AICHESTRA_NO_REAL_QUOTA="1")
        def run(*args):
            return subprocess.run([str(a) for a in args], cwd=project, env=env,
                                  check=True, capture_output=True, text=True).stdout
        run(python, "-m", "pip", "install", "--no-deps", wheel)
        print(run(cli, "--version").strip())
        run(cli, "init", "--yes")
        run(cli, "settings", "set", "roles.implement=cursor", "quota.mode=auto")
        settings = json.loads(run(cli, "settings", "show"))
        assert settings["roles"]["implement"]["runtime"] == "cursor"
        assert settings["quota"]["mode"] == "auto"
        location = run(python, "-c", "import aichestra; print(aichestra.__file__)").strip()
        assert Path(location).resolve().is_relative_to(env_dir.resolve())
        print("Wheel installed and CLI verified outside checkout")


if __name__ == "__main__":
    main()
