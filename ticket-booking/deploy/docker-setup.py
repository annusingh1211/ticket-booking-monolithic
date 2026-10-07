#!/usr/bin/env python3
import os, subprocess, sys


def run(cmd, shell=False):
    print(f"\n$ {cmd if isinstance(cmd, str) else ' '.join(cmd)}")
    subprocess.run(cmd, shell=shell, check=True)


def sudo(cmd):
    return cmd if os.geteuid() == 0 else ["sudo", *cmd]


def main():
    if sys.platform != "linux":
        raise SystemExit("ERROR: This script is for Ubuntu/Linux.")

    print("=" * 70)
    print("Docker Engine + Docker Compose Setup")
    print("=" * 70)

    run(sudo(["apt-get", "update"]))
    run(sudo(["apt-get", "install", "-y", "ca-certificates", "curl"]))

    run(sudo(["install", "-m", "0755", "-d", "/etc/apt/keyrings"]))
    run(sudo(["curl", "-fsSL", "https://download.docker.com/linux/ubuntu/gpg",
              "-o", "/etc/apt/keyrings/docker.asc"]))
    run(sudo(["chmod", "a+r", "/etc/apt/keyrings/docker.asc"]))

    repo = ('deb [arch=$(dpkg --print-architecture) '
            'signed-by=/etc/apt/keyrings/docker.asc] '
            'https://download.docker.com/linux/ubuntu '
            '$(. /etc/os-release && echo "$VERSION_CODENAME") stable')
    run(f'echo "{repo}" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null', shell=True)

    run(sudo(["apt-get", "update"]))
    run(sudo(["apt-get", "install", "-y", "docker-ce", "docker-ce-cli",
              "containerd.io", "docker-buildx-plugin", "docker-compose-plugin"]))

    run(sudo(["systemctl", "enable", "--now", "docker"]))

    user = os.environ.get("SUDO_USER") or os.environ.get("USER")
    if user and user != "root":
        run(sudo(["usermod", "-aG", "docker", user]))

    print("\n--- Verification ---")
    run(["docker", "--version"])
    run(["docker", "compose", "version"])
    run(sudo(["systemctl", "is-active", "docker"]))

    print("\nSETUP COMPLETE")
    if user and user != "root":
        print(f"User '{user}' was added to the docker group.")
        print("Reconnect to SSH, then run:")
        print("  docker run hello-world")
        print("  cd ~/ticket-booking")
        print("  docker compose config")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        print(f"\nERROR: command failed with exit code {e.returncode}")
        sys.exit(e.returncode)
    except KeyboardInterrupt:
        print("\nSetup cancelled.")
        sys.exit(130)
