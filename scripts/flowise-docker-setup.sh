#!/bin/bash
# flowise-docker-setup.sh
# Install Docker (if needed) and run Flowise in a container on macOS

# 1. Install Homebrew if it is not present
if ! command -v brew >/dev/null 2>&1; then
    echo "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

# 2. Install Docker Desktop via Homebrew (this includes Docker CLI and Docker Compose)
if ! command -v docker >/dev/null 2>&1; then
    echo "Installing Docker..."
    brew install --cask docker
    # Docker Desktop for Mac requires manual startup on first install
    open /Applications/Docker.app
    echo "Please wait for Docker Desktop to finish starting before continuing..."
    read -p "Press Enter once Docker Desktop is running." _
fi

# 3. Pull the Flowise image from Docker Hub
docker pull flowiseai/flowise

# 4. Launch Flowise container
docker run -d \
    -p 3000:3000 \
    -v $HOME/.flowise:/root/.flowise \
    --name flowise-server \
    flowiseai/flowise

echo "Flowise is now running at http://localhost:3000"
