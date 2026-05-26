# 🎙️ AI Interpreter — Real-Time English-Vietnamese Translation

Hệ thống thông dịch viên AI thời gian thực (Real-time AI Interpreter) sử dụng **FastAPI (Python)** ở Backend và **HTML/CSS/JS thuần (Glassmorphism)** ở Frontend.

Dự án sử dụng phương pháp tối ưu: **Gemini Multimodal Audio API**. Trình duyệt ghi âm và gửi trực tiếp luồng âm thanh qua WebSocket lên Backend, Backend truyền thẳng luồng âm thanh đó sang mô hình đa phương thức **Gemini 1.5 Flash** để phiên âm và dịch đồng thời.

---

## ✨ Tính năng chính

- **🎙️ Ghi âm trực tiếp từ trình duyệt**: Thu âm luồng PCM thời gian thực qua WebSocket.
- **⚡ VAD cục bộ (Voice Activity Detection)**: Phát hiện khoảng lặng tự động trên frontend để chia nhỏ câu nói thông minh, tối ưu hóa lượt gọi API.
- **🧠 Dịch thuật đa phương thức song song**: Sử dụng Gemini 1.5 Flash xử lý trực tiếp âm thanh thô mà không cần cài đặt các thư viện dịch thuật/STT C++ nặng nề khác.
- **📺 Giao diện Glassmorphism cao cấp**: Thiết kế thẩm mỹ với các hiệu ứng chuyển động, hiển thị kết quả dịch song song 2 cột Tiếng Anh & Tiếng Việt.
- **🔄 Cơ chế tự động thử lại (Retry Backoff)**: Chống lỗi 429 khi tài khoản chạm hạn mức giới hạn (Rate limits).

---

## 📁 Cấu trúc thư mục

```text
Problem_1_The_AI_Interpreter/
├── backend/
│   ├── main.py              # Server FastAPI kết nối WebSocket và Gemini API
│   └── requirements.txt     # Các thư viện Python cốt lõi
├── frontend/
│   ├── index.html           # Giao diện Glassmorphism cao cấp
│   └── app.js               # Logic thu âm, VAD cục bộ và truyền WebSocket
├── .env                     # Cấu hình chứa API Key (không commit lên git)
├── .env.example             # File cấu hình mẫu
└── README.md                # Tài liệu hướng dẫn này
```

---

## 🚀 Hướng dẫn Cài đặt & Vận hành

### Yêu cầu hệ thống
* **Python 3.9 - 3.11** (khuyên dùng Python 3.10).
* Trình duyệt hỗ trợ WebRTC/Audio Recording (Chrome, Edge, Safari...).

### Bước 1: Tạo Môi Trường Ảo (Virtual Environment)
Mở Terminal (PowerShell trên Windows) tại thư mục `Problem_1_The_AI_Interpreter` và chạy:
```bash
python -m venv venv
```

Kích hoạt môi trường ảo:
* **Windows (PowerShell)**:
  ```powershell
  .\venv\Scripts\Activate.ps1
  ```
* **Windows (CMD)**:
  ```cmd
  .\venv\Scripts\activate.bat
  ```
* **macOS/Linux**:
  ```bash
  source venv/bin/activate
  ```

### Bước 2: Cài đặt các thư viện Python
```bash
pip install -r backend/requirements.txt
```

### Bước 3: Thiết lập Cấu hình `.env`
Sao chép tệp mẫu hoặc tạo file `.env` tại thư mục gốc `Problem_1_The_AI_Interpreter/.env`:
```env
# Chế độ giả lập (True = chạy test thử nghiệm không cần API Key, False = dùng API thật)
MOCK_MODE=True

# API Key Gemini từ https://aistudio.google.com/app/apikey (Chỉ cần khi MOCK_MODE=False)
GEMINI_API_KEY=your_gemini_api_key_here
```

### Bước 4: Khởi động Backend Server
```bash
uvicorn backend.main:app --reload
```
* Server sẽ khởi chạy tại địa chỉ: `http://127.0.0.1:8000`

### Bước 5: Mở Giao diện Frontend
Mở trình duyệt Web và truy cập vào:
👉 **[http://127.0.0.1:8000/static/index.html](http://127.0.0.1:8000/static/index.html)**

---

## 📊 Hướng dẫn Kiểm tra Hệ thống (Testing)

1. **Chế độ giả lập (Simulation Mode - MOCK_MODE=True)**:
   * Nhấp nút **Record** màu tím ở giữa màn hình và nói thử.
   * Sau khi bạn dừng nói ~1.8 giây (VAD phát hiện khoảng lặng), frontend sẽ gửi gói tin đi và server trả về kết quả giả lập hiển thị trên màn hình.
   * Chế độ này giúp bạn kiểm tra nhanh micro và luồng WebSocket hoạt động chính xác mà không tốn quota API.

2. **Chế độ dịch thật (Simulation Mode - MOCK_MODE=False)**:
   * Cấu hình `MOCK_MODE=False` và điền `GEMINI_API_KEY` hợp lệ vào `.env` (hoặc dán trực tiếp API Key trên giao diện sidebar).
   * Tắt nút gạt *Simulation Mode* trên giao diện.
   * Nhấn **Record** và bắt đầu nói bằng Tiếng Anh hoặc Tiếng Việt. Khi dừng nói, hệ thống sẽ dịch tự động sang ngôn ngữ còn lại trong vòng chưa đầy 2 giây!
