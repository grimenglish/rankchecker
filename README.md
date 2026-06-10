# Product Rank Tracker

네이버 쇼핑 + 쿠팡 상품 순위를 한 화면에서 체크합니다.

## 파일
- app.py
- requirements.txt
- README.md
- 윈도우_처음실행.bat

## PC 실행
```bash
pip install -r requirements.txt
python -m playwright install chromium
streamlit run app.py
```

## 참고
- 네이버: 공식 API 방식
- 쿠팡: 브라우저 자동화 방식
- Streamlit Cloud에서는 쿠팡이 차단될 수 있어 PC 실행 권장
