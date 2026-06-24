#!/usr/bin/env python3
"""
AUTOMATIC SETUP SCRIPT
Fixes all common issues and sets up the bot
"""

import subprocess
import sys
import os

def run_command(command, description):
    """Run a command and show status"""
    print(f"\n{'='*70}")
    print(f"🔧 {description}")
    print(f"{'='*70}")
    print(f"Running: {command}\n")
    
    try:
        result = subprocess.run(
            command,
            shell=True,
            check=True,
            capture_output=True,
            text=True
        )
        print(result.stdout)
        print(f"✅ {description} - SUCCESS\n")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ {description} - FAILED")
        print(f"Error: {e.stderr}\n")
        return False

def check_python_version():
    """Check if Python version is adequate"""
    version = sys.version_info
    print(f"\n🐍 Python Version: {version.major}.{version.minor}.{version.micro}")
    
    if version.major < 3 or (version.major == 3 and version.minor < 8):
        print("❌ Python 3.8+ required!")
        print("Please upgrade Python: https://www.python.org/downloads/")
        return False
    
    print("✅ Python version OK")
    return True

def main():
    """Main setup function"""
    
    print("\n" + "="*70)
    print("🚀 TELEGRAM TRANSLATION BOT - AUTOMATIC SETUP")
    print("="*70)
    
    # Check Python version
    if not check_python_version():
        sys.exit(1)
    
    # Step 1: Upgrade pip
    run_command(
        f"{sys.executable} -m pip install --upgrade pip",
        "Upgrading pip"
    )
    
    # Step 2: Uninstall old openai
    print("\n🔄 Removing old OpenAI library...")
    subprocess.run(
        f"{sys.executable} -m pip uninstall openai -y",
        shell=True,
        capture_output=True
    )
    
    # Step 3: Install core dependencies
    packages = [
        "openai",
        "telethon",
        "flask",
        "python-dotenv",
        "requests",
        "langdetect"
    ]
    
    for package in packages:
        run_command(
            f"{sys.executable} -m pip install {package} --upgrade",
            f"Installing {package}"
        )
    
    # Step 4: Verify installations
    print("\n" + "="*70)
    print("✅ VERIFICATION")
    print("="*70 + "\n")
    
    verification_passed = True
    
    # Test OpenAI
    try:
        from openai import OpenAI
        print("✅ OpenAI - OK")
    except ImportError as e:
        print(f"❌ OpenAI - FAILED: {e}")
        verification_passed = False
    
    # Test Telethon
    try:
        from telethon import TelegramClient
        print("✅ Telethon - OK")
    except ImportError as e:
        print(f"❌ Telethon - FAILED: {e}")
        verification_passed = False
    
    # Test Flask
    try:
        from flask import Flask
        print("✅ Flask - OK")
    except ImportError as e:
        print(f"❌ Flask - FAILED: {e}")
        verification_passed = False
    
    # Final report
    print("\n" + "="*70)
    if verification_passed:
        print("🎉 SETUP COMPLETE!")
        print("="*70)
        print("\n✅ All dependencies installed successfully!")
        print("\n📋 NEXT STEPS:")
        print("   1. Update config.py with your API keys")
        print("   2. Run: python telegram_bot_groups.py")
        print("   3. In another terminal: python dashboard_groups.py")
        print("\n📚 For detailed guide, read: SETUP_GUIDE_HINDI.md")
        print("\n" + "="*70 + "\n")
    else:
        print("⚠️  SETUP INCOMPLETE")
        print("="*70)
        print("\n❌ Some packages failed to install")
        print("Please check errors above and:")
        print("   1. Make sure you have internet connection")
        print("   2. Try running as administrator (Windows) or with sudo (Linux)")
        print("   3. Check Python version is 3.8+")
        print("\n" + "="*70 + "\n")
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Setup interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Setup failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)