# 🎯 Gainer Dump Detector — Setup Guide

## สิ่งที่ต้องทำ (ทำครั้งเดียว ~10 นาที)

---

### 1. สร้าง GitHub Repo

1. ไปที่ https://github.com/new
2. ตั้งชื่อ repo: `gainer-dump-detector`
3. เลือก **Public** (ต้องเป็น public เพื่อใช้ GitHub Pages ฟรี)
4. กด **Create repository**

---

### 2. Push Code ขึ้น Repo

เปิด Terminal แล้วรันคำสั่ง:

```bash
cd ~/Downloads/AI\ Trader\ Persona\ \&\ Skill\ Profile_\ The\ Precision\ Hunter/gainer_dump_detector

git init
git add .
git commit -m "initial: gainer dump detector"
git branch -M main
git remote add origin https://github.com/kidkurobar/gainer-dump-detector.git
git push -u origin main
```

---

### 3. ตั้ง Secrets (Token ต่างๆ)

1. ไปที่ repo → **Settings** → **Secrets and variables** → **Actions**
2. กด **New repository secret** เพิ่มทีละตัว:

| Name | Value |
|------|-------|
| `TELEGRAM_TOKEN` | `8837408072:AAE4TDTrLnXHI4G79QcNMpU0Cj_O7IT4zRo` |
| `TELEGRAM_CHAT_ID` | `6652792902` |

---

### 4. เปิด GitHub Pages

1. ไปที่ repo → **Settings** → **Pages**
2. Source: เลือก **Deploy from a branch**
3. Branch: เลือก **main** → folder: **`/docs`**
4. กด **Save**

---

### 5. เปิด GitHub Actions Permissions

1. ไปที่ repo → **Settings** → **Actions** → **General**
2. ส่วน **Workflow permissions**: เลือก **Read and write permissions**
3. กด **Save**

---

### 6. ทดสอบ

1. ไปที่ repo → tab **Actions**
2. เลือก workflow **Gainer Dump Detector**
3. กด **Run workflow** → **Run workflow** (ปุ่มเขียว)
4. รอ ~1-2 นาที แล้วเช็ค:
   - ✅ Workflow สำเร็จ (เครื่องหมายถูกเขียว)
   - ✅ Dashboard: `https://kidkurobar.github.io/gainer-dump-detector/`
   - ✅ Telegram: ได้รับ alert (ถ้ามีสัญญาณ HIGH)

---

## การทำงาน

- GitHub Actions รัน scan **ทุก 5 นาที** อัตโนมัติ
- ผลลัพธ์ update ไปที่ **GitHub Pages** (เปิดดูจากมือถือ/คอมที่ไหนก็ได้)
- ถ้าเจอ **HIGH confidence dump signal** → ส่ง alert เข้า **Telegram** ทันที
- ไม่ต้องเปิดคอม ไม่ต้องเปิด Terminal

---

## ปรับแต่ง

แก้ไฟล์ `scan_dump.py`:

| ค่า | ค่าเริ่มต้น | ความหมาย |
|-----|------------|---------|
| `TOP_GAINERS` | 30 | จำนวน top gainers ที่สแกน |
| `RSI_OB` | 70 | RSI overbought threshold |
| `DIV_LOOKBACK` | 20 | bars ย้อนหลังเช็ค divergence |
| `VOL_LOOKBACK` | 10 | bars ย้อนหลังเช็ค volume |

แก้ไฟล์ `.github/workflows/scan.yml`:

| ค่า | ค่าเริ่มต้น | ความหมาย |
|-----|------------|---------|
| `cron: '*/5 * * * *'` | ทุก 5 นาที | ความถี่ในการสแกน |

---

## หมายเหตุ

- GitHub Actions free tier: 2,000 นาที/เดือน (ทุก 5 นาที ≈ 8,640 runs = ~720 นาที ✅ พอ)
- Dashboard auto-refresh ทุก 5 นาทีในเบราว์เซอร์
- ไม่มีค่าใช้จ่าย
