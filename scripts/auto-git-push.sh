#!/bin/bash
cd ~/sci-cluster
source ~/.bashrc
now=$(date +"%Y-%m-%d %H:%M:%S")
git add .
git commit -m "Auto commit on $now."
git push origin main
