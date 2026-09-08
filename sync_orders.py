import os
import requests
from app import app, db, Order, API_URL, API_KEY

def sync_pending_orders():
    """
    Background job to sync pending orders with God of Panel API.
    Since the API accepts single 'order' IDs, requests are sent sequentially,
    and a single bulk commit is performed at the end.
    """
    with app.app_context():
        # Fetch all pending/in-progress orders across all users
        pending_orders = Order.query.filter(
            Order.status.in_(['Pending', 'In progress', 'Processing']),
            Order.api_order_id.isnot(None)
        ).all()

        if not pending_orders:
            print("No pending orders found to sync.")
            return

        print(f"Found {len(pending_orders)} pending orders to sync...")
        updated_count = 0

        # Session for connection reuse
        session = requests.Session()

        for order in pending_orders:
            try:
                response = session.post(
                    API_URL,
                    data={
                        'key': API_KEY,
                        'action': 'status',
                        'order': order.api_order_id
                    },
                    timeout=5
                )
                
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, dict) and 'status' in data:
                        new_status = data.get('status')
                        remains = data.get('remains', getattr(order, 'remains', None))

                        if order.status != new_status or (hasattr(order, 'remains') and order.remains != remains):
                            order.status = new_status
                            if hasattr(order, 'remains'):
                                order.remains = remains
                            updated_count += 1
                            print(f"Order #{order.id} updated -> Status: {new_status}")
            except Exception as e:
                print(f"Failed to sync Order #{order.id} (API ID: {order.api_order_id}): {str(e)}")
                continue

        # Single atomic database commit at the end
        if updated_count > 0:
            try:
                db.session.commit()
                print(f"Successfully committed {updated_count} updated orders to Database.")
            except Exception as e:
                db.session.rollback()
                print(f"Database commit error: {str(e)}")
        else:
            print("No order status changes detected.")

if __name__ == "__main__":
    sync_pending_orders()
