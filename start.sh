#!/bin/bash
# Ensure LF line endings
# Navigate to your bot directory first
cd "$(dirname "$0")"

# Activate virtual environment if you have one (optional)
# source venv/bin/activate

# Run the bot
python3 bot.py
