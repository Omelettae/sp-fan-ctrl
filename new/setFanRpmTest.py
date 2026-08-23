import requests
 
response = requests.post(
    "http://back-end-ip:5000/api/actuatorCommand",
    json={
        "actuatorID": 1,
        "action": "SET_SPEED",
        "pwmDutyPercent": 0
    }
)
print(response.status_code)
print(response.json())
