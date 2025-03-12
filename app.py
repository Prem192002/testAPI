from fastapi import FastAPI, HTTPException, Header, Request, Depends
import logging
import os
import razorpay
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
import hmac
import hashlib
import boto3
from boto3.dynamodb.conditions import Key
from decimal import Decimal
from datetime import datetime, timezone, timedelta

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize FastAPI app
app = FastAPI()

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Razorpay API credentials
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_API_KEY")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_API_SECRET")

if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
    logger.error("Razorpay API credentials are missing.")
    raise ValueError("Razorpay API credentials are not set.")

# Initialize Razorpay client
razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

# Initialize DynamoDB
dynamodb = boto3.resource("dynamodb", region_name="ap-south-1")
subscription_table = dynamodb.Table("subscription")

async def extract_bearer_token(request: Request, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid or missing Authorization token")
    
    token = authorization.split("Bearer ")[1].strip()
    request.state.user_token = token
    logger.info(f"Extracted Bearer token: {token}")
    return token

@app.post("/create-order")
async def create_order(request: Request, _: str = Depends(extract_bearer_token)):
    try:
        data = await request.json()
        user_id = data.get("user_id")
        logger.info(f"Received request with user_id: {user_id}")

        if not user_id:
            logger.error("User ID is missing in the request payload")
            raise HTTPException(status_code=400, detail="User ID is required in the request payload")

        subscription = subscription_table.get_item(Key={"user_id": user_id}).get("Item")
        if not subscription:
            now = datetime.now(timezone.utc)
            credit_amount = Decimal("1000")
            subscription_table.put_item(
                Item={
                    "user_id": user_id,
                    "credit_amount": credit_amount,
                    "remaining_amount": None,
                    "subscription_status": "pending",
                    "order_id": "",
                    "start_date": now.isoformat(),
                    "expiry_date": (now + timedelta(days=30)).isoformat(),
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "last_billed_at": None,
                    "subscription_banned": False
                }
            )
            logger.info(f"Created new subscription for user_id: {user_id} with subscription_status: pending")
            subscription = subscription_table.get_item(Key={"user_id": user_id}, ConsistentRead=True).get("Item")

        credit_amount = Decimal(str(subscription["credit_amount"]))
        amount_in_paise = int(credit_amount * 100)

        logger.info(f"Creating order for user_id: {user_id}, credit_amount: {credit_amount}")

        order_data = {
            "amount": amount_in_paise,
            "currency": "INR",
            "payment_capture": 1
        }
        razorpay_order = razorpay_client.order.create(data=order_data)
        order_id = razorpay_order["id"]
        logger.info(f"Generated order_id: {order_id} for user_id: {user_id}")

        subscription_table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET order_id = :o, subscription_status = :s, updated_at = :u",
            ExpressionAttributeValues={
                ":o": order_id,
                ":s": "pending",
                ":u": datetime.now(timezone.utc).isoformat()
            }
        )
        logger.info(f"Updated user_id: {user_id} with order_id: {order_id}, subscription_status: pending")

        updated_subscription = subscription_table.get_item(Key={"user_id": user_id}, ConsistentRead=True).get("Item")
        if updated_subscription["subscription_status"] != "pending":
            logger.error(f"Failed to set subscription_status to pending for user_id: {user_id}. Current status: {updated_subscription['subscription_status']}")
            raise HTTPException(status_code=500, detail=f"Failed to update subscription_status to pending. Current status: {updated_subscription['subscription_status']}")

        return {"order_id": order_id, "message": "Razorpay order created and subscription updated successfully"}

    except Exception as e:
        logger.error(f"Error creating order: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

@app.post("/payment/verify")
async def verify_payment(request: Request, _: str = Depends(extract_bearer_token)):
    try:
        data = await request.json()
        payment_info = data.get("payment_info")
        logger.info(f"Received payment verification request: {payment_info}")

        if not payment_info:
            logger.error("Missing payment_info in request")
            raise HTTPException(status_code=400, detail="Missing payment_info")

        razorpay_payment_id = payment_info.get("razorpay_payment_id")
        razorpay_order_id = payment_info.get("razorpay_order_id")
        razorpay_signature = payment_info.get("razorpay_signature")

        if not razorpay_payment_id or not razorpay_order_id or not razorpay_signature:
            logger.error("Missing required payment details")
            raise HTTPException(status_code=400, detail="Missing required payment details")

        if not verify_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature):
            logger.error(f"Payment verification failed for order_id: {razorpay_order_id}")
            return {"success": False, "message": "Payment verification failed"}

        response = subscription_table.scan(
            FilterExpression="order_id = :o",
            ExpressionAttributeValues={":o": razorpay_order_id}
        )
        items = response.get("Items", [])
        if not items:
            logger.error(f"No subscription found for order_id: {razorpay_order_id}")
            raise HTTPException(status_code=404, detail=f"No subscription found for order_id: {razorpay_order_id}")

        subscription = items[0]
        user_id = subscription["user_id"]
        credit_amount = Decimal(str(subscription["credit_amount"]))

        now = datetime.now(timezone.utc)
        new_start_date = now
        new_expiry_date = now + timedelta(days=30)

        # Update subscription and clear order_id (set to empty string)
        subscription_table.update_item(
            Key={"user_id": user_id},
            UpdateExpression=(
                "SET remaining_amount = :r, subscription_status = :s, start_date = :start, "
                "expiry_date = :expiry, updated_at = :u, subscription_banned = :b, order_id = :o"
            ),
            ExpressionAttributeValues={
                ":r": credit_amount,
                ":s": "active",
                ":start": new_start_date.isoformat(),
                ":expiry": new_expiry_date.isoformat(),
                ":u": now.isoformat(),
                ":b": False,
                ":o": ""  # Clear order_id data, keep the field
            }
        )
        logger.info(
            f"Verified payment for user_id: {user_id}, updated: "
            f"remaining_amount={credit_amount}, subscription_status=active, "
            f"start_date={new_start_date.isoformat()}, expiry_date={new_expiry_date.isoformat()}, "
            f"order_id cleared to ''"
        )

        # Verify the update
        updated_subscription = subscription_table.get_item(Key={"user_id": user_id}, ConsistentRead=True).get("Item")
        if updated_subscription["subscription_status"] != "active" or updated_subscription["remaining_amount"] != credit_amount:
            logger.error(
                f"Failed to update subscription for user_id: {user_id}. "
                f"Current subscription_status: {updated_subscription['subscription_status']}, "
                f"remaining_amount: {updated_subscription['remaining_amount']}"
            )
            raise HTTPException(status_code=500, detail="Failed to update subscription fields")
        if updated_subscription["order_id"] != "":
            logger.error(f"Failed to clear order_id for user_id: {user_id}. Current value: {updated_subscription['order_id']}")
            raise HTTPException(status_code=500, detail="Failed to clear order_id")

        return {"success": True, "message": "Payment verified successfully, subscription updated, order_id cleared"}

    except Exception as e:
        logger.error(f"Payment verification error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

def verify_signature(order_id: str, payment_id: str, signature: str) -> bool:
    try:
        generated_signature = hmac.new(
            RAZORPAY_KEY_SECRET.encode(),
            f"{order_id}|{payment_id}".encode(),
            hashlib.sha256
        ).hexdigest()
        logger.info(f"Generated signature: {generated_signature}, Received signature: {signature}")
        return generated_signature == signature
    except Exception as e:
        logger.error(f"Error verifying Razorpay signature: {str(e)}")
        return False