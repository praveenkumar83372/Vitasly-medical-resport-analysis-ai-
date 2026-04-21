from google import genai

client = genai.Client(api_key="AIzaSyAzS8-irRriZcDmHoIK-8CZP3ZnXd-0jmU")

res = client.models.generate_content(
    model="gemini-1.5-flash",
    contents="Say hello"
)

print(res.text)