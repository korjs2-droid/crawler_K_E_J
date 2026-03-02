# Gov Multilingual News Crawler

한국어/일본어/영어 공공·정부 뉴스 피드(RSS/Atom)를 수집하는 Flask 웹앱입니다.

## 대상 소스

- 한국어: 정책브리핑 (대한민국 정부) RSS
- 일본어: 経済産業省 (METI) 뉴스 릴리스 RSS
- 영어: NASA News Releases RSS (U.S. government)

## 로컬 실행

```bash
cd gov-news-crawler
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

브라우저에서 `http://127.0.0.1:5000` 접속.

## 프로덕션 실행 (Gunicorn)

```bash
cd gov-news-crawler
pip install -r requirements.txt
PORT=5000 gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 60
```

## Docker 배포

```bash
cd gov-news-crawler
docker build -t gov-news-crawler .
docker run --rm -p 5000:5000 gov-news-crawler
```

## Render 배포

1. Render에서 `New +` -> `Web Service`
2. 저장소 연결 후 루트 디렉터리를 `gov-news-crawler`로 지정
3. `render.yaml` 사용 또는 아래 수동 입력
4. Build Command: `pip install -r requirements.txt`
5. Start Command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 60`

## GitHub로 퍼블리시

`gov-news-crawler` 폴더를 별도 GitHub 저장소로 올리는 예시입니다.

```bash
cd gov-news-crawler
git init
git add .
git commit -m "Initial publish: gov multilingual crawler"
git branch -M main
git remote add origin https://github.com/<YOUR_ID>/<REPO_NAME>.git
git push -u origin main
```

업로드 후 GitHub Actions(`.github/workflows/ci.yml`)가 자동으로 실행되어 기본 검증을 수행합니다.

그다음 Render에서 해당 GitHub 저장소를 연결하면 `main` 브랜치 푸시마다 자동 재배포됩니다.

## 기능

- 소스 선택형 크롤링 (KR/JP/EN)
- 키워드 필터
- 수집 개수 제한 (1~50)
- RSS + Atom 공통 파싱

## 주의

- 이 프로젝트는 각 기관의 공개 피드 정책을 존중하는 범위에서 사용해야 합니다.
- 대량 수집/상업적 재배포 전에는 반드시 robots.txt, 이용약관, 저작권 정책을 재확인하세요.
