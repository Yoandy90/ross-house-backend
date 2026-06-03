"""
Push Notification Service - Hybrid Expo + Firebase
===================================================
Routes ExponentPushToken tokens to Expo Push API,
and FCM tokens to Firebase Cloud Messaging.
"""
import logging
from typing import List, Optional, Dict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def is_expo_token(token: str) -> bool:
    """Check if a token is an Expo push token"""
    return token and isinstance(token, str) and token.startswith('ExponentPushToken[')


class PushNotificationService:
    """Service for sending push notifications via Expo Push API and Firebase"""
    
    def __init__(self):
        self.firebase_service = None
        self._init_firebase()
    
    def _init_firebase(self):
        try:
            from firebase_push_service import firebase_push_service
            self.firebase_service = firebase_push_service
            if self.firebase_service.is_initialized:
                logger.info("✅ Push Notification Service: Firebase ready")
            else:
                logger.warning("⚠️ Firebase not initialized")
        except ImportError:
            logger.warning("⚠️ Firebase service not available")
            self.firebase_service = None
    
    async def _send_via_expo(self, tokens: List[str], title: str, body: str,
                              data: Optional[Dict] = None, sound: str = "default",
                              priority: str = "high") -> Dict:
        """Send via Expo Push API for ExponentPushToken tokens"""
        import os
        import requests
        
        EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
        EXPO_ACCESS_TOKEN = os.getenv("EXPO_ACCESS_TOKEN", "")
        
        expo_tokens = [t for t in tokens if is_expo_token(t)]
        if not expo_tokens:
            return {"success": False, "message": "No Expo tokens", "sent_count": 0, "failed_count": 0}
        
        # Deduplicate tokens
        expo_tokens = list(set(expo_tokens))
        
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        
        # Add access token if available - this scopes requests to the correct project
        # and avoids PUSH_TOO_MANY_EXPERIENCE_IDS errors
        if EXPO_ACCESS_TOKEN:
            headers["Authorization"] = f"Bearer {EXPO_ACCESS_TOKEN}"
        
        # Send tokens individually to avoid PUSH_TOO_MANY_EXPERIENCE_IDS error
        total_sent = 0
        total_failed = 0
        
        for token in expo_tokens:
            message = {
                "to": token,
                "title": title,
                "body": body,
                "data": data or {},
                "sound": sound,
                "priority": priority,
                "channelId": "default"
            }
            
            try:
                response = requests.post(
                    EXPO_PUSH_URL,
                    json=[message],
                    headers=headers,
                    timeout=15
                )
                
                if response.status_code == 200:
                    result = response.json()
                    results_data = result.get("data", [])
                    if results_data and results_data[0].get("status") == "ok":
                        total_sent += 1
                    else:
                        total_failed += 1
                        err_msg = results_data[0].get("message", "") if results_data else ""
                        logger.warning(f"⚠️ Expo push failed for token {token[:30]}...: {err_msg}")
                else:
                    # Try to handle PUSH_TOO_MANY_EXPERIENCE_IDS gracefully
                    err_body = response.json() if response.headers.get('content-type', '').startswith('application/json') else {}
                    errors = err_body.get("errors", [])
                    if any(e.get("code") == "PUSH_TOO_MANY_EXPERIENCE_IDS" for e in errors):
                        logger.warning(f"⚠️ Token {token[:30]}... belongs to different project, skipping")
                    else:
                        total_failed += 1
                        logger.warning(f"⚠️ Expo API error {response.status_code} for token {token[:30]}...")
            except Exception as e:
                total_failed += 1
                logger.error(f"❌ Expo push error for token: {e}")
        
        logger.info(f"📬 Expo Push: {total_sent} sent, {total_failed} failed out of {len(expo_tokens)}")
        
        return {
            "success": total_sent > 0,
            "sent_count": total_sent,
            "failed_count": total_failed,
            "results": []
        }
    
    async def _send_via_firebase(self, tokens: List[str], title: str, body: str,
                                   data: Optional[Dict] = None) -> Dict:
        """Send via Firebase for FCM tokens"""
        if not self.firebase_service or not self.firebase_service.is_initialized:
            return {"success": False, "message": "Firebase not initialized", "sent_count": 0}
        
        # Convert data values to strings (Firebase requirement)
        string_data = {}
        if data:
            for key, value in data.items():
                string_data[key] = str(value) if value is not None else ""
        
        try:
            result = await self.firebase_service.send_to_multiple_devices(
                tokens=tokens,
                title=title,
                body=body,
                data=string_data
            )
            
            success_count = result.get('success_count', 0)
            logger.info(f"🔥 Firebase: {success_count} sent, {result.get('failure_count', 0)} failed")
            
            return {
                "success": result.get('success', False),
                "sent_count": success_count,
                "failed_count": result.get('failure_count', 0),
                "results": result
            }
        except Exception as e:
            logger.error(f"❌ Firebase error: {e}")
            return {"success": False, "error": str(e), "sent_count": 0}
    
    async def send_push_notification(
        self,
        push_tokens: List[str],
        title: str,
        body: str,
        data: Optional[Dict] = None,
        sound: str = "default",
        priority: str = "high"
    ) -> Dict:
        """
        Send push notification - routes to Expo or Firebase based on token type
        """
        if not push_tokens:
            logger.warning("No push tokens provided")
            return {"success": False, "message": "No tokens provided", "sent_count": 0}
        
        valid_tokens = [t for t in push_tokens if t and isinstance(t, str) and len(t) > 10]
        if not valid_tokens:
            logger.warning("No valid push tokens")
            return {"success": False, "message": "No valid tokens", "sent_count": 0}
        
        # Split tokens by type
        expo_tokens = [t for t in valid_tokens if is_expo_token(t)]
        fcm_tokens = [t for t in valid_tokens if not is_expo_token(t)]
        
        total_sent = 0
        total_failed = 0
        all_results = []
        
        # Send to Expo tokens
        if expo_tokens:
            expo_result = await self._send_via_expo(expo_tokens, title, body, data, sound, priority)
            total_sent += expo_result.get('sent_count', 0)
            total_failed += expo_result.get('failed_count', 0)
            all_results.append({'provider': 'expo', **expo_result})
        
        # Send to FCM tokens via Firebase
        if fcm_tokens and self.firebase_service and self.firebase_service.is_initialized:
            fcm_result = await self._send_via_firebase(fcm_tokens, title, body, data)
            total_sent += fcm_result.get('sent_count', 0)
            total_failed += fcm_result.get('failed_count', 0)
            all_results.append({'provider': 'firebase', **fcm_result})
        elif fcm_tokens:
            logger.warning(f"⚠️ {len(fcm_tokens)} FCM tokens but Firebase not available")
            total_failed += len(fcm_tokens)
        
        logger.info(f"📊 Push total: {total_sent} sent, {total_failed} failed ({len(expo_tokens)} Expo, {len(fcm_tokens)} FCM)")
        
        return {
            "success": total_sent > 0,
            "sent_count": total_sent,
            "failed_count": total_failed,
            "total_tokens": len(valid_tokens),
            "results": all_results
        }
    
    async def send_to_user(
        self,
        user_push_token: str = None,
        title: str = "",
        body: str = "",
        data: Optional[Dict] = None,
        # Also accept db+user_id pattern for backward compatibility
        db=None,
        user_id: str = None
    ) -> Dict:
        """Send push notification to a single user"""
        
        # If db and user_id provided, look up the token
        if db is not None and user_id and not user_push_token:
            user = await db.users.find_one({'_id': user_id})
            if not user:
                try:
                    from bson import ObjectId
                    user = await db.users.find_one({'_id': ObjectId(user_id)})
                except:
                    pass
            
            if not user:
                return {"success": False, "message": f"User {user_id} not found"}
            
            user_push_token = user.get('push_token') or user.get('expo_push_token')
            if not user_push_token:
                return {"success": False, "message": f"User has no push token"}
        
        if not user_push_token:
            return {"success": False, "message": "No push token provided"}
        
        return await self.send_push_notification(
            push_tokens=[user_push_token],
            title=title,
            body=body,
            data=data
        )
    
    async def send_to_all_users(
        self,
        db,
        title: str,
        body: str,
        data: Optional[Dict] = None,
        role: Optional[str] = None
    ) -> Dict:
        """Send push notification to all users with push tokens"""
        try:
            query = {
                '$or': [
                    {'push_token': {'$exists': True, '$ne': None, '$ne': ''}},
                    {'expo_push_token': {'$exists': True, '$ne': None, '$ne': ''}}
                ]
            }
            
            if role:
                query['role'] = role
            
            users = await db.users.find(query).to_list(10000)
            
            if not users:
                return {"success": False, "message": "No users with push tokens found", "sent_count": 0}
            
            # Extract all push tokens
            push_tokens = []
            for user in users:
                token = user.get('push_token') or user.get('expo_push_token')
                if token:
                    push_tokens.append(token)
            
            if not push_tokens:
                return {"success": False, "message": "No valid push tokens found", "sent_count": 0}
            
            result = await self.send_push_notification(
                push_tokens=push_tokens,
                title=title,
                body=body,
                data=data
            )
            
            result['users_queried'] = len(users)
            return result
            
        except Exception as e:
            logger.error(f"Error sending push to all users: {e}")
            return {"success": False, "error": str(e), "sent_count": 0}
    
    async def send_to_segment(
        self,
        db,
        segment_type: str,
        title: str,
        body: str,
        data: Optional[Dict] = None,
        language: Optional[str] = None
    ) -> Dict:
        """Send push notification to a specific segment of users"""
        from datetime import timedelta
        
        try:
            # Base: users with any kind of push token
            base_query = {
                '$or': [
                    {'push_token': {'$exists': True, '$ne': None, '$ne': ''}},
                    {'expo_push_token': {'$exists': True, '$ne': None, '$ne': ''}}
                ]
            }
            
            if language:
                base_query['preferred_language'] = language
            
            user_ids = []
            
            if segment_type == 'pending_documents':
                pending_docs = await db.document_requests.find({'status': 'pending'}).to_list(10000)
                user_ids = list(set([str(doc.get('user_id')) for doc in pending_docs if doc.get('user_id')]))
                
            elif segment_type == 'upcoming_appointments':
                now = datetime.utcnow()
                week_later = now + timedelta(days=7)
                appointments = await db.appointments.find({
                    'date': {'$gte': now, '$lte': week_later},
                    'status': {'$nin': ['cancelled', 'completed']}
                }).to_list(10000)
                user_ids = list(set([str(apt.get('user_id') or apt.get('client_id')) for apt in appointments]))
                
            elif segment_type == 'overdue_invoices':
                invoices = await db.invoices.find({'status': 'overdue'}).to_list(10000)
                user_ids = list(set([str(inv.get('user_id') or inv.get('client_id')) for inv in invoices]))
                
            elif segment_type == 'incomplete_profile':
                incomplete = await db.users.find({
                    'role': 'client',
                    '$or': [
                        {'phone': {'$exists': False}},
                        {'phone': None},
                        {'phone': ''},
                        {'address': {'$exists': False}}
                    ]
                }).to_list(10000)
                user_ids = [str(u['_id']) for u in incomplete]
                
            elif segment_type == 'inactive_30_days':
                thirty_days_ago = datetime.utcnow() - timedelta(days=30)
                inactive = await db.users.find({
                    'role': 'client',
                    '$or': [
                        {'last_login': {'$lt': thirty_days_ago}},
                        {'last_login': {'$exists': False}}
                    ]
                }).to_list(10000)
                user_ids = [str(u['_id']) for u in inactive]
                
            elif segment_type == 'new_users_7_days':
                seven_days_ago = datetime.utcnow() - timedelta(days=7)
                new_users = await db.users.find({
                    'created_at': {'$gte': seven_days_ago}
                }).to_list(10000)
                user_ids = [str(u['_id']) for u in new_users]
                
            elif segment_type == 'credits_low':
                low_credits = await db.users.find({
                    'role': 'client',
                    'credits': {'$lt': 50}
                }).to_list(10000)
                user_ids = [str(u['_id']) for u in low_credits]
                
            elif segment_type in ('all_clients', 'active_clients'):
                base_query['role'] = 'client'
                users = await db.users.find(base_query).to_list(10000)
                push_tokens = []
                for u in users:
                    t = u.get('push_token') or u.get('expo_push_token')
                    if t:
                        push_tokens.append(t)
                return await self.send_push_notification(push_tokens, title, body, data)
                
            elif segment_type == 'all_admins':
                base_query['role'] = {'$in': ['admin', 'office_assistant']}
                users = await db.users.find(base_query).to_list(10000)
                push_tokens = []
                for u in users:
                    t = u.get('push_token') or u.get('expo_push_token')
                    if t:
                        push_tokens.append(t)
                return await self.send_push_notification(push_tokens, title, body, data)
            
            else:
                return {"success": False, "message": f"Unknown segment: {segment_type}", "sent_count": 0}
            
            # If we collected user_ids, fetch their push tokens
            if user_ids:
                user_query = {'_id': {'$in': user_ids}}
                users = await db.users.find(user_query).to_list(10000)
                push_tokens = []
                for u in users:
                    t = u.get('push_token') or u.get('expo_push_token')
                    if t:
                        push_tokens.append(t)
                
                if not push_tokens:
                    return {"success": False, "message": "No push tokens in segment", "sent_count": 0, "segment_size": len(user_ids)}
                
                result = await self.send_push_notification(push_tokens, title, body, data)
                result['segment_size'] = len(user_ids)
                result['tokens_found'] = len(push_tokens)
                return result
            
            return {"success": False, "message": "No users found for segment", "sent_count": 0}
            
        except Exception as e:
            logger.error(f"Error sending push to segment {segment_type}: {e}")
            return {"success": False, "error": str(e), "sent_count": 0}
    
    async def get_segment_stats(self, db) -> Dict:
        """Get statistics for each segment"""
        from datetime import timedelta
        
        try:
            stats = {}
            
            stats['pending_documents'] = await db.document_requests.count_documents({'status': 'pending'})
            
            now = datetime.utcnow()
            week_later = now + timedelta(days=7)
            stats['upcoming_appointments'] = await db.appointments.count_documents({
                'date': {'$gte': now, '$lte': week_later},
                'status': {'$nin': ['cancelled', 'completed']}
            })
            
            stats['overdue_invoices'] = await db.invoices.count_documents({'status': 'overdue'})
            
            stats['incomplete_profile'] = await db.users.count_documents({
                'role': 'client',
                '$or': [
                    {'phone': {'$exists': False}},
                    {'phone': None},
                    {'phone': ''}
                ]
            })
            
            thirty_days_ago = datetime.utcnow() - timedelta(days=30)
            stats['inactive_30_days'] = await db.users.count_documents({
                'role': 'client',
                '$or': [
                    {'last_login': {'$lt': thirty_days_ago}},
                    {'last_login': {'$exists': False}}
                ]
            })
            
            seven_days_ago = datetime.utcnow() - timedelta(days=7)
            stats['new_users_7_days'] = await db.users.count_documents({
                'created_at': {'$gte': seven_days_ago}
            })
            
            stats['credits_low'] = await db.users.count_documents({
                'role': 'client',
                'credits': {'$lt': 50}
            })
            
            # Count with push tokens (either type)
            push_query = {
                '$or': [
                    {'push_token': {'$exists': True, '$ne': None, '$ne': ''}},
                    {'expo_push_token': {'$exists': True, '$ne': None, '$ne': ''}}
                ]
            }
            
            client_push_query = {**push_query, 'role': 'client'}
            admin_push_query = {**push_query, 'role': {'$in': ['admin', 'office_assistant']}}
            
            stats['all_clients'] = await db.users.count_documents(client_push_query)
            stats['all_admins'] = await db.users.count_documents(admin_push_query)
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting segment stats: {e}")
            return {}


# Singleton instance
_push_service = None

def get_push_service() -> PushNotificationService:
    """Get or create push notification service singleton"""
    global _push_service
    if _push_service is None:
        _push_service = PushNotificationService()
    return _push_service


async def send_push_notification(
    user_id: str = None,
    expo_push_token: str = None,
    title: str = "",
    body: str = "",
    data: Optional[Dict] = None
) -> bool:
    """
    Helper function to send push notification to a user.
    Can use either user_id (fetches token from DB) or direct expo_push_token.
    """
    try:
        service = get_push_service()
        
        if expo_push_token:
            result = await service.send_to_user(
                user_push_token=expo_push_token,
                title=title,
                body=body,
                data=data
            )
            return result.get("success", False)
        
        if user_id:
            from motor.motor_asyncio import AsyncIOMotorClient
            import os
            
            mongo_url = os.getenv('MONGO_URL', 'mongodb://localhost:27017')
            client = AsyncIOMotorClient(mongo_url)
            db = client['taxportal']
            
            # Try string ID first (UUID), then ObjectId
            user = await db.users.find_one({'_id': user_id})
            if not user:
                try:
                    from bson import ObjectId
                    if ObjectId.is_valid(user_id):
                        user = await db.users.find_one({'_id': ObjectId(user_id)})
                except:
                    pass
            
            if not user:
                logger.warning(f"User {user_id} not found")
                return False
            
            push_token = user.get('push_token') or user.get('expo_push_token')
            if not push_token:
                logger.warning(f"User {user_id} has no push token")
                return False
            
            result = await service.send_to_user(
                user_push_token=push_token,
                title=title,
                body=body,
                data=data
            )
            return result.get("success", False)
        
        logger.warning("Neither user_id nor expo_push_token provided")
        return False
        
    except Exception as e:
        logger.error(f"Error in send_push_notification: {e}")
        return False
