"""
Telegram Group Creator with Dynamic Configuration
Uses Telethon library to interact with Telegram Client API
"""

from telethon import TelegramClient, functions, types
from telethon.tl.types import ChatBannedRights
from dotenv import load_dotenv
import asyncio
from typing import List, Dict, Optional
import os


class TelegramGroupManager:
    """
    Manages Telegram group creation and user permissions
    """
    
    def __init__(self, api_id: int, api_hash: str, phone_number: str):
        """
        Initialize Telegram client
        
        Args:
            api_id: Your Telegram API ID from https://my.telegram.org
            api_hash: Your Telegram API Hash
            phone_number: Your phone number with country code
        """
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone_number = phone_number
        self.client = TelegramClient('session', api_id, api_hash)
    
    async def connect(self):
        """Connect and authenticate with Telegram"""
        await self.client.connect()
        
        if not await self.client.is_user_authorized():
            await self.client.send_code_request(self.phone_number)
            code = input('Enter the code you received: ')
            await self.client.sign_in(self.phone_number, code)
    
    async def create_group(
        self,
        title: str,
        about: str = "",
        users: List[str] = None,
        is_megagroup: bool = True
    ) -> Dict:
        """
        Create a new Telegram group with dynamic configuration
        
        Args:
            title: Group name
            about: Group description
            users: List of usernames to add (without @)
            is_megagroup: True for supergroup, False for basic group
            
        Returns:
            Dictionary with group information
        """
        try:
            # Resolve users to add
            user_entities = []
            if users:
                for username in users:
                    try:
                        user = await self.client.get_entity(username)
                        user_entities.append(user)
                    except Exception as e:
                        print(f"Warning: Could not find user {username}: {e}")
            
            # Need at least one user to create a group
            if not user_entities:
                print("Warning: At least one user is required. Adding self.")
                me = await self.client.get_me()
                user_entities = [me]
            
            # Create the group/supergroup
            result = await self.client(functions.messages.CreateChatRequest(
                users=user_entities,
                title=title
            ))
            
            # Get the created chat
            chat_id = None
            for chat in result.chats:
                chat_id = chat.id
                break
            
            # If we want a megagroup (supergroup), migrate it
            if is_megagroup and chat_id:
                try:
                    migrate_result = await self.client(
                        functions.messages.MigrateChatRequest(chat_id=chat_id)
                    )
                    # Get the new supergroup ID
                    for chat in migrate_result.chats:
                        if hasattr(chat, 'megagroup') and chat.megagroup:
                            chat_id = chat.id
                            break
                except Exception as e:
                    print(f"Could not migrate to supergroup: {e}")
            
            # Set the description/about
            if about and chat_id:
                try:
                    await self.client(functions.messages.EditChatAboutRequest(
                        peer=chat_id,
                        about=about
                    ))
                except Exception as e:
                    print(f"Could not set description: {e}")
            
            return {
                'success': True,
                'chat_id': chat_id,
                'title': title,
                'about': about
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    async def set_user_permissions(
        self,
        chat_id: int,
        username: str,
        permissions: Dict[str, bool]
    ) -> Dict:
        """
        Set specific permissions for a user in the group
        
        Args:
            chat_id: The chat/group ID
            username: Username of the user (without @)
            permissions: Dictionary of permission settings
                Example: {
                    'send_messages': True,
                    'send_media': True,
                    'send_stickers': False,
                    'send_polls': True,
                    'add_users': False,
                    'pin_messages': False,
                    'change_info': False
                }
        
        Returns:
            Dictionary with operation result
        """
        try:
            # Get the user entity
            user = await self.client.get_entity(username)
            
            # Create banned rights (inverse of permissions)
            banned_rights = ChatBannedRights(
                until_date=None,  # Permanent
                view_messages=False,  # Can view
                send_messages=not permissions.get('send_messages', True),
                send_media=not permissions.get('send_media', True),
                send_stickers=not permissions.get('send_stickers', True),
                send_gifs=not permissions.get('send_stickers', True),
                send_games=not permissions.get('send_stickers', True),
                send_inline=not permissions.get('send_stickers', True),
                send_polls=not permissions.get('send_polls', True),
                change_info=not permissions.get('change_info', False),
                invite_users=not permissions.get('add_users', False),
                pin_messages=not permissions.get('pin_messages', False)
            )
            
            # Apply the permissions
            await self.client(functions.channels.EditBannedRequest(
                channel=chat_id,
                participant=user,
                banned_rights=banned_rights
            ))
            
            return {
                'success': True,
                'username': username,
                'permissions': permissions
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    async def promote_admin(
        self,
        chat_id: int,
        username: str,
        admin_rights: Dict[str, bool] = None
    ) -> Dict:
        """
        Promote a user to admin with specific rights
        
        Args:
            chat_id: The chat/group ID
            username: Username to promote (without @)
            admin_rights: Dictionary of admin rights
                Example: {
                    'change_info': True,
                    'delete_messages': True,
                    'ban_users': True,
                    'invite_users': True,
                    'pin_messages': True,
                    'add_admins': False,
                    'manage_call': True
                }
        
        Returns:
            Dictionary with operation result
        """
        try:
            if admin_rights is None:
                admin_rights = {
                    'change_info': True,
                    'delete_messages': True,
                    'ban_users': True,
                    'invite_users': True,
                    'pin_messages': True,
                    'add_admins': False,
                    'manage_call': True
                }
            
            user = await self.client.get_entity(username)
            
            # Create admin rights object
            rights = types.ChatAdminRights(
                change_info=admin_rights.get('change_info', False),
                post_messages=admin_rights.get('post_messages', False),
                edit_messages=admin_rights.get('edit_messages', False),
                delete_messages=admin_rights.get('delete_messages', False),
                ban_users=admin_rights.get('ban_users', False),
                invite_users=admin_rights.get('invite_users', False),
                pin_messages=admin_rights.get('pin_messages', False),
                add_admins=admin_rights.get('add_admins', False),
                manage_call=admin_rights.get('manage_call', False)
            )
            
            # Promote the user
            await self.client(functions.channels.EditAdminRequest(
                channel=chat_id,
                user_id=user,
                admin_rights=rights,
                rank='Admin'
            ))
            
            return {
                'success': True,
                'username': username,
                'admin_rights': admin_rights
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    async def disconnect(self):
        """Disconnect from Telegram"""
        await self.client.disconnect()


# Example usage
async def main():
    """Example of how to use the TelegramGroupManager"""
    
    # Credentials loaded from environment variables / .env file
    # Never hardcode these — use your .env file instead
    from dotenv import load_dotenv
    load_dotenv()
    API_ID   = int(os.getenv('TELEGRAM_API_ID',  '0'))
    API_HASH = os.getenv('TELEGRAM_API_HASH', '')
    PHONE    = os.getenv('TELEGRAM_PHONE',   '')
    
    # Initialize manager
    manager = TelegramGroupManager(API_ID, API_HASH, PHONE)
    
    try:
        # Connect to Telegram
        await manager.connect()
        
        # Dynamic group configuration from "frontend"
        group_config = {
            'title': 'My Dynamic Group',
            'about': 'This is a group created via API with custom settings',
            'users': ['username1', 'username2']  # Add initial members
        }
        
        # Create the group
        result = await manager.create_group(
            title=group_config['title'],
            about=group_config['about'],
            users=group_config['users']
        )
        
        if result['success']:
            chat_id = result['chat_id']
            print(f"Group created successfully! ID: {chat_id}")
            
            # Set custom permissions for a user
            permissions_config = {
                'send_messages': True,
                'send_media': True,
                'send_stickers': False,
                'send_polls': True,
                'add_users': False,
                'pin_messages': False,
                'change_info': False
            }
            
            perm_result = await manager.set_user_permissions(
                chat_id=chat_id,
                username='username1',
                permissions=permissions_config
            )
            
            if perm_result['success']:
                print(f"Permissions set for user!")
            
            # Promote someone to admin
            admin_rights_config = {
                'change_info': True,
                'delete_messages': True,
                'ban_users': True,
                'invite_users': True,
                'pin_messages': True,
                'add_admins': False,
                'manage_call': True
            }
            
            admin_result = await manager.promote_admin(
                chat_id=chat_id,
                username='username2',
                admin_rights=admin_rights_config
            )
            
            if admin_result['success']:
                print(f"User promoted to admin!")
        else:
            print(f"Error creating group: {result['error']}")
            
    finally:
        # Disconnect
        await manager.disconnect()


if __name__ == '__main__':
    asyncio.run(main())