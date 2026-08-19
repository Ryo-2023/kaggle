FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON=3.11 \
    PATH="/opt/venv/bin:/root/.local/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        curl \
        git \
        openssh-client \
        pkg-config \
        unzip \
        wget \
    && rm -rf /var/lib/apt/lists/*

# GitHub's published Ed25519 host key. Keeping this in the system known-hosts
# file avoids interactive first-contact prompts without copying host SSH state
# or disabling StrictHostKeyChecking.
RUN mkdir -p /etc/ssh /etc/ssh/ssh_config.d \
    && printf '%s\n' \
        'github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl' \
        > /etc/ssh/ssh_known_hosts \
    && printf '%s\n' \
        'Host github.com' \
        '    IdentityAgent /run/host-services/ssh-auth.sock' \
        > /etc/ssh/ssh_config.d/99-biohub-github-agent.conf

# Persistent interactive Bash prompt:
#   user@host: bright green (92), path: blue (34), Git branch: yellow (33),
#   prompt character: white (37). Non-printing ANSI sequences are wrapped in \[...\]
#   so Readline keeps cursor positioning correct.
RUN printf '%s\n' \
        '' \
        '# Biohub persistent colored shell prompt.' \
        '__biohub_git_branch() {' \
        '    local branch' \
        '    branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null)" || return 0' \
        '    printf " (%s)" "$branch"' \
        '}' \
        'PS1='\''\[\e[92m\]\u@\h\[\e[0m\]:\[\e[34m\]\w\[\e[0m\]\[\e[33m\]$(__biohub_git_branch)\[\e[0m\]\[\e[37m\]\$\[\e[0m\] '\''' \
        >> /root/.bashrc

RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && uv python install 3.11

WORKDIR /workspace/biohub-cell-tracking-during-development

COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests

RUN uv sync

CMD ["sleep", "infinity"]
