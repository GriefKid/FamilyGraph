@echo off
cd /d "%~dp0"
echo ====================================================
echo   FamilyGraph — Migration + Server
echo ====================================================
echo.

echo [1/2] اجرای patch_db.py (اضافه کردن ستون‌های جدید)...
python patch_db.py
echo.

echo [2/2] راه‌اندازی سرور...
cd FamilyGraph
python manage.py runserver
pause
