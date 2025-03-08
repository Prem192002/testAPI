import boto3
import os
import hmac
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
import razorpay
from dotenv import load_dotenv
from boto3.dynamodb.conditions import Key
from decimal import Decimal

# Load environment variables
load_dotenv()

# Initialize FastAPI
app = FastAPI()

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Initialize Razorpay client
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_API_KEY")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_API_SECRET")
BASE_URL = "http://127.0.0.1:8000"

if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
    logger.error("Razorpay API credentials are missing from environment variables.")
    raise RuntimeError("Razorpay API credentials are required.")

razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

# Initialize AWS DynamoDB
dynamodb = boto3.resource("dynamodb", region_name="ap-south-1")
transaction_table = dynamodb.Table("transaction")
subscription_table = dynamodb.Table("subscription")

# Initialize templates
templates = Jinja2Templates(directory="templates")

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Subscription type to duration mapping (in months)
SUBSCRIPTION_DURATIONS = {
    "monthly": 1,
    "quarterly": 3,
    "yearly": 12
}

def calculate_expiry_date(start_date, subscription_type):
    """Calculate expiry date based on subscription type."""
    months = SUBSCRIPTION_DURATIONS.get(subscription_type.lower(), 1)  # Default to 1 month if unknown
    expiry_date = start_date + timedelta(days=30 * months)  # Approximate months as 30 days
    return expiry_date.isoformat()

@app.get("/")
async def serve_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/create-subscription")
async def create_subscription(request: Request):
    data = await request.json()
    user_id = data.get("user_id")
    amount = data.get("amount")
    subscription_type = data.get("subscription_type")

    if not all([user_id, amount, subscription_type]):
        raise HTTPException(status_code=400, detail="Missing required fields")
    
    try:
        amount = Decimal(str(amount))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid amount format")

    subscription_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    subscription_table.put_item(
        Item={
            "subscription_id": subscription_id,
            "user_id": user_id,
            "razorpay_order_id": "",
            "amount": amount,
            "payment_status": "pending",
            "subscription_type": subscription_type,
            "start_date": None,
            "expiry_date": None,
            "created_at": created_at,
            "updated_at": created_at
        }
    )
    return {"message": "Subscription created. Please authorize.", "subscription_id": subscription_id}

@app.post("/create-order")
async def create_order(request: Request):
    data = await request.json()
    user_id = data.get("user_id")

    if not user_id:
        raise HTTPException(status_code=400, detail="User ID is required")
    
    response = subscription_table.query(
        IndexName="user_id-index",
        KeyConditionExpression=Key("user_id").eq(user_id),
        ScanIndexForward=False
    )
    items = response.get("Items", [])

    if not items:
        raise HTTPException(status_code=404, detail="No active subscription found for this user")
    
    item = items[0]
    
    try:
        amount_in_paise = int(Decimal(str(item["amount"])) * Decimal(100))
    except ValueError:
        raise HTTPException(status_code=500, detail="Subscription amount is invalid")

    try:
        razorpay_order = razorpay_client.order.create(data={"amount": amount_in_paise, "currency": "INR", "payment_capture": 1})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create Razorpay order: {str(e)}")

    subscription_table.update_item(
        Key={"subscription_id": item["subscription_id"]},
        UpdateExpression="SET razorpay_order_id = :o, updated_at = :u",
        ExpressionAttributeValues={":o": razorpay_order["id"], ":u": datetime.now(timezone.utc).isoformat()}
    )

    transaction_table.put_item(
        Item={
            "razorpay_order_id": razorpay_order["id"],
            "user_id": user_id,
            "subscription_id": item["subscription_id"],
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
    )
    return {"order_id": razorpay_order["id"], "message": "Razorpay order created successfully"}

@app.post("/payment-verify")
async def verify_payment(request: Request):
    data = await request.json()
    logger.info(f"Received payment verification request: {json.dumps(data)}")

    payment_info = data.get("payment_info", {})
    razorpay_order_id = payment_info.get("razorpay_order_id")
    razorpay_payment_id = payment_info.get("razorpay_payment_id")
    razorpay_signature = payment_info.get("razorpay_signature")

    if not razorpay_order_id or not razorpay_payment_id or not razorpay_signature:
        raise HTTPException(status_code=400, detail="Missing payment details.")
    
    if not RAZORPAY_KEY_SECRET:
        logger.error("Razorpay API secret is missing from environment variables.")
        raise HTTPException(status_code=500, detail="Server configuration error.")
    
    secret = bytes(RAZORPAY_KEY_SECRET, 'utf-8')
    generated_signature = hmac.new(secret, f"{razorpay_order_id}|{razorpay_payment_id}".encode(), hashlib.sha256).hexdigest()
    
    if generated_signature != razorpay_signature:
        transaction_table.update_item(
            Key={"razorpay_order_id": razorpay_order_id},
            UpdateExpression="SET #status = :status, updated_at = :updated_at",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":status": "failed", ":updated_at": datetime.now(timezone.utc).isoformat()}
        )
        raise HTTPException(status_code=400, detail="Invalid payment signature.")

    # Update transaction table
    transaction_table.update_item(
        Key={"razorpay_order_id": razorpay_order_id},
        UpdateExpression="SET #status = :status, payment_id = :payment_id, updated_at = :updated_at",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":status": "success",
            ":payment_id": razorpay_payment_id,
            ":updated_at": datetime.now(timezone.utc).isoformat()
        }
    )
    
    # Fetch transaction to get subscription_id and subscription_type
    transaction = transaction_table.get_item(Key={"razorpay_order_id": razorpay_order_id}).get("Item", {})
    if transaction:
        subscription_id = transaction["subscription_id"]
        subscription = subscription_table.get_item(Key={"subscription_id": subscription_id}).get("Item", {})
        if subscription:
            start_date = datetime.now(timezone.utc)
            expiry_date = calculate_expiry_date(start_date, subscription["subscription_type"])
            
            subscription_table.update_item(
                Key={"subscription_id": subscription_id},
                UpdateExpression="SET payment_status = :status, start_date = :start, expiry_date = :expiry, updated_at = :updated_at",
                ExpressionAttributeValues={
                    ":status": "active",
                    ":start": start_date.isoformat(),
                    ":expiry": expiry_date,
                    ":updated_at": datetime.now(timezone.utc).isoformat()
                }
            )
    
    return {"message": "Payment verified, transaction updated, and subscription activated successfully."}