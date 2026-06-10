# Product Rank Tracker

네이버 쇼핑 + 쿠팡 상품 순위를 한 화면에서 체크하는 Streamlit 앱입니다.

## 실행

```bash
pip install -r requirements.txt
python -m playwright install chromium
streamlit run app.py
```

## 주의

- 네이버: 공식 검색 API 사용
- 쿠팡: 브라우저 자동화 사용
- 쿠팡은 Streamlit Cloud에서 차단될 수 있어 PC 실행 권장
