#!/bin/bash
cd ~/sci-uster
source ~/.bashrc
now=$(date +"%Y-%m-%d %H:%M:%S")
git add .
git commit -m "Auto commit on $now."
git push origin main
