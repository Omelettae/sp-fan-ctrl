import requests

response = requests.post(
    "http://back-end-ip:5000/api/setFanRPM",
    json={
        "sensorID": 1,
        "targetRPM": 0
    }
)

print(response.status_code)
print(response.json())
