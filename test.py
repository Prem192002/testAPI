import hmac
import hashlib

# Replace this with your actual webhook secret from Razorpay
RAZORPAY_WEBHOOK_SECRET = "prem@2002"

# Copy the exact JSON payload from Postman into this variable
payload = '''{
    "entity": "event",
    "account_id": "acc_PuPlWCTBCJ8KmC",
    "event": "payment.captured",
    "contains": ["payment"],
    "payload": {
        "payment": {
            "entity": {
                "id": "pay_PzXtYmOwpLHoGN",
                "entity": "payment",
                "amount": 400000,
                "currency": "INR",
                "status": "captured",
                "order_id": "order_PzXt8OK453IUUu",
                "method": "card",
                "email": "prem@example.com",
                "contact": "+919999999999",
                "created_at": 1740399680
            }
        }
    },
    "created_at": 1740399691
}'''

# Generate the signature using HMAC SHA256
generated_signature = hmac.new(
    bytes(RAZORPAY_WEBHOOK_SECRET, 'utf-8'),
    bytes(payload, 'utf-8'),
    hashlib.sha256
).hexdigest()

print("Generated Signature:", generated_signature)
