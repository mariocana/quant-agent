@echo off
call C:\Users\TuoUtente\miniconda3\Scripts\activate.bat propagent
cd C:\propagent
uvicorn dashboard.api:app --host 0.0.0.0 --port 8000
