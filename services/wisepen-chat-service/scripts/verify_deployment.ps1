Write-Host "=== compose services ==="
docker compose -f docker-compose-app.yml ps

Write-Host "`n=== chat-service env ==="
docker exec wisepen-chat-service printenv HF_HOME
docker exec wisepen-chat-service printenv TRANSLATION_DEVICE
docker exec wisepen-chat-service printenv MATH_SAGE_ENABLED

Write-Host "`n=== subprocess CLI dependencies ==="
docker exec wisepen-chat-service pandoc --version
docker exec wisepen-chat-service node --version
docker exec wisepen-chat-service npm --version
docker exec wisepen-chat-service node -e "require('rebrowser-playwright'); require('jsdom'); require('turndown'); require('@mozilla/readability'); console.log('web_fetch node deps ok')"

Write-Host "`n=== translation model cache ==="
docker exec wisepen-chat-service python -c "from transformers import MarianTokenizer, MarianMTModel; MarianTokenizer.from_pretrained('Helsinki-NLP/opus-mt-zh-en'); MarianMTModel.from_pretrained('Helsinki-NLP/opus-mt-zh-en'); print('zh-en ok')"
docker exec wisepen-chat-service python -c "from transformers import MarianTokenizer, MarianMTModel; MarianTokenizer.from_pretrained('Helsinki-NLP/opus-mt-en-zh'); MarianMTModel.from_pretrained('Helsinki-NLP/opus-mt-en-zh'); print('en-zh ok')"

Write-Host "`n=== sage ==="
docker exec sage-math-worker sage -python -c "from sage.all import power_mod; print(power_mod(2,100,17))"

Write-Host "`n=== sage health ==="
docker exec sage-math-worker sage -python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health', timeout=5).read().decode())"

Write-Host "`n=== done ==="
