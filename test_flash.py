import asyncio
from google import genai
from google.genai import types
import os
from dotenv import load_dotenv
load_dotenv('/Users/jakedibattista/Code/CodeStuff/AuditMySite/agent-backend/.env')
client = genai.Client()
async def test():
    tasks = []
    # Create a dummy image
    image_bytes = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xfa\xff\xff\xff\x00\x00\x08\xfc\x02\xfe\xa1\xa8\x04\x9a\x00\x00\x00\x00IEND\xaeB`\x82'
    
    for i in range(5):
        prompt_parts = ['hello ' * 1000, types.Part.from_bytes(data=image_bytes, mime_type='image/png')]
        tasks.append(client.aio.models.generate_content(model='gemini-3-flash-preview', contents=prompt_parts))
    try:
        resps = await asyncio.gather(*tasks, return_exceptions=True)
        for i, r in enumerate(resps):
            if isinstance(r, Exception):
                print(f'error {i}: {r}')
            else:
                print(f'success {i}')
    except Exception as e:
        print(f'Exception: {e}')
asyncio.run(test())
