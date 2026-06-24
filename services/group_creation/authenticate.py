"""
Telegram Authentication Setup Script
Run this first to authenticate your Telegram account
"""

from telethon import TelegramClient
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

# Get credentials from environment variables
API_ID = int(os.getenv('TELEGRAM_API_ID', '0'))
API_HASH = os.getenv('TELEGRAM_API_HASH', '')
PHONE = os.getenv('TELEGRAM_PHONE', '')


async def authenticate():
    """Authenticate with Telegram"""
    
    print("=" * 60)
    print("🔐 Telegram Authentication Setup")
    print("=" * 60)
    
    # Validate credentials
    if API_ID == 0 or not API_HASH or not PHONE:
        print("\n❌ Error: Missing credentials!")
        print("\nPlease set up your .env file with:")
        print("  - TELEGRAM_API_ID")
        print("  - TELEGRAM_API_HASH")
        print("  - TELEGRAM_PHONE")
        print("\nGet your API credentials from: https://my.telegram.org/apps")
        return False
    
    print(f"\n📱 Phone Number: {PHONE}")
    print(f"🔑 API ID: {API_ID}")
    print(f"🔐 API Hash: {API_HASH[:10]}...")
    
    # Create client
    client = TelegramClient('session', API_ID, API_HASH)
    
    try:
        await client.connect()
        
        if not await client.is_user_authorized():
            print("\n📨 Sending authentication code to your phone...")
            await client.send_code_request(PHONE)
            
            print("\n✅ Code sent!")
            code = input("\n🔢 Enter the code you received: ")
            
            try:
                await client.sign_in(PHONE, code)
                print("\n✅ Authentication successful!")
                print("\n📄 Session file created: session.session")
                print("\n🎉 You can now run the Flask app: python app.py")
                return True
                
            except Exception as e:
                print(f"\n❌ Error signing in: {e}")
                
                # Check if 2FA is enabled
                if "password" in str(e).lower():
                    password = input("\n🔐 2FA is enabled. Enter your password: ")
                    await client.sign_in(password=password)
                    print("\n✅ Authentication successful!")
                    print("\n📄 Session file created: session.session")
                    print("\n🎉 You can now run the Flask app: python app.py")
                    return True
                else:
                    raise
        else:
            print("\n✅ Already authenticated!")
            print("\n🎉 You can run the Flask app: python app.py")
            return True
            
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return False
        
    finally:
        await client.disconnect()


def check_env_file():
    """Check if .env file exists and has required variables"""
    if not os.path.exists('.env'):
        print("\n⚠️  .env file not found!")
        print("\n📝 Creating .env file from template...")
        
        # Create .env from template
        with open('.env', 'w') as f:
            f.write("""# Telegram API Credentials
# Get these from https://my.telegram.org/apps
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=your_api_hash_here
TELEGRAM_PHONE=+1234567890

# Flask Configuration
FLASK_ENV=development
FLASK_DEBUG=1
""")
        
        print("\n✅ .env file created!")
        print("\n📝 Please edit .env file with your credentials:")
        print("   1. Go to https://my.telegram.org/apps")
        print("   2. Log in with your phone number")
        print("   3. Create a new application")
        print("   4. Copy your API ID and API Hash")
        print("   5. Update the .env file")
        print("\nThen run this script again: python authenticate.py")
        return False
    
    return True


def main():
    """Main function"""
    
    if not check_env_file():
        return
    
    # Run authentication
    asyncio.run(authenticate())


if __name__ == '__main__':
    main()