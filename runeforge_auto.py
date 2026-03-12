import requests
from bs4 import BeautifulSoup
import re
import os
import subprocess
import json
import sys

# === Cấu hình Terminal Unicode ===
sys.stdout.reconfigure(encoding='utf-8')

# === THIẾT LẬP CỦA ANH ===
BASE_URL = "https://runeforge.dev/mods"

# Sử dụng biến môi trường Github Action, nếu không có thì fallback sang cục bộ
GITHUB_WORKSPACE = os.environ.get("GITHUB_WORKSPACE", r"D:\O\RuneForge-ModKeeper")
DOWNLOAD_DIR = GITHUB_WORKSPACE
MAX_MB_PER_PUSH = 999
MAX_FILES_PER_DIR = 990 
SCRAPED_LOG_FILE = os.path.join(GITHUB_WORKSPACE, "_runeforge_scraped.json")

# Khi chạy trên Github Action thì có thể để None, nhưng vẫn giới hạn nhẹ lúc test
TEST_LIMIT = None

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
}

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Lịch sử các UUID đã được tải thành công để không cào lại mỗi ngày
scraped_history = []
if os.path.exists(SCRAPED_LOG_FILE):
    try:
        with open(SCRAPED_LOG_FILE, "r", encoding="utf-8") as f:
            scraped_history = json.load(f)
    except:
        scraped_history = []

def save_history():
    with open(SCRAPED_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(scraped_history, f, indent=4)

# === CÁC HÀM CÀO DỮ LIỆU ===
def get_recent_mods(limit=None):
    print("📌 Đang lấy danh sách Mods mới nhất...", flush=True)
    all_links = []
    page = 1
    
    while True:
        try:
            r = requests.get(f"{BASE_URL}?page={page}", headers=headers, timeout=15)
            if r.status_code != 200:
                print(f"Trang {page} trả về mã {r.status_code}. Dừng quét.", flush=True)
                break
                
            matches = re.findall(r'/mods/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})', r.text)
            matches = list(set(matches)) # Unique links trên 1 trang
            
            new_mods_found = 0
            for mod_id in matches:
                if mod_id not in all_links:
                    all_links.append(mod_id)
                    new_mods_found += 1
                    
                if limit and len(all_links) >= limit:
                    break
                    
            print(f"  Trang {page}: Tìm thấy {new_mods_found} bài viết mới.", flush=True)
                    
            if limit and len(all_links) >= limit:
                break
                
            # Nếu tất cả các Mod trong trang này đã có mặt trong danh sách lịch sử của Runeforge (nghĩa là không có Mod nào mới hoàn toàn ở list cào)
            if new_mods_found == 0:
                print(f"  Trang {page} không chứa bài viết mới nào. Kết thúc phân trang sớm (Anti-Infinity-Loop).", flush=True)
                break
                
            page += 1
        except Exception as e:
            print(f"Lỗi phân trang {page}: {e}", flush=True)
            break
            
    print(f"🔍 Quét được tổng cộng {len(all_links)} bài viết.", flush=True)
    return all_links

def clean_filename(name):
    # Dọn dẹp ký tự không hợp lệ cho Windows/Linux path
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()

def get_mod_info(mod_id):
    """
    Truy cập vào trang bài viết để lấy Tên Bài Viết (Tên Mod)
    """
    url = f"{BASE_URL}/{mod_id}"
    try:
        r = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        
        mod_name = mod_id # Fallback
        
        # Thử lấy từ Meta OG
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            mod_name = og_title['content'].replace(" | Runeforge", "").strip()
        elif soup.title:
            mod_name = soup.title.string.replace(" | Runeforge", "").strip()
            
        mod_name = clean_filename(mod_name)
        if not mod_name: mod_name = mod_id
        
        return mod_name
    except:
        return mod_id

def get_download_links(mod_id):
    url = f"{BASE_URL}/{mod_id}/releases"
    try:
        r = requests.get(url, headers=headers, timeout=15)
        links = set()
        matches = re.findall(r'https://[^"\']*?\.fantome', r.text)
        if not matches:
            matches = re.findall(r'https://r2-prod\.runeforge\.dev[^"\']*', r.text)
        for m in matches:
            links.add(m)
        return list(links)
    except Exception as e:
        print(f"  ... Lỗi tìm Release: {e}", flush=True)
        return []

def download_file(url, target_path):
    print(f"  📥 Tải tệp: {os.path.basename(target_path)}...", flush=True)
    try:
        with requests.get(url, stream=True, headers=headers, timeout=60) as r:
            r.raise_for_status()
            with open(target_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return os.path.getsize(target_path)
    except Exception as e:
        print(f"  ❌ Lỗi tải: {e}", flush=True)
        if os.path.exists(target_path):
            os.remove(target_path)
        return 0

# === QUẢN LÝ THƯ MỤC PART_X & DUNG LƯỢNG ===
def count_files_in_dir(dir_path):
    """ Đệ quy đếm toàn bộ số lượng tệp hiện có trong một thư mục (Folder) """
    if not os.path.exists(dir_path):
        return 0
    file_count = 0
    for root, _, files in os.walk(dir_path):
        file_count += len(files)
    return file_count

def get_target_dir(mod_name):
    """
    Quy tắc Túi Lớn Part_X: Mod cũ thì về túi cũ.
    Mod mới thì vào túi mới nhất nếu dung lượng túi đó còn (tổng các mod <= 990 files).
    Đầy thì nhảy qua túi tiếp theo.
    """
    # 1. Quét tìm xem Mod này đã từng tồn tại trong túi bào chưa? (Hưởng ứng theo nguyện vọng update mod cũ của chủ nhân)
    part_dirs = [d for d in os.listdir(DOWNLOAD_DIR) if os.path.isdir(os.path.join(DOWNLOAD_DIR, d)) and d.startswith("Part")]
    
    # Sắp xếp để check từ Part lớn nhất (mới nhất) về Part số 1
    part_dirs.sort(key=lambda x: int(x.replace("Part", "")) if x.replace("Part", "").isdigit() else 0, reverse=True)
    
    for part in part_dirs:
        mod_path = os.path.join(DOWNLOAD_DIR, part, mod_name)
        if os.path.exists(mod_path):
            print(f"  📂 Phát hiện túi chứa Mod cũ: {part} -> Nhét thẳng vào đây.", flush=True)
            return mod_path

    # 2. Nếu là Mod mới toanh, thì xét Túi Chứa (Part_X) mới nhất
    latest_part_num = 1
    if part_dirs:
        latest_part_num = int(part_dirs[0].replace("Part", "")) if part_dirs[0].replace("Part", "").isdigit() else 1
        
    latest_part_dir = os.path.join(DOWNLOAD_DIR, f"Part{latest_part_num}")
    os.makedirs(latest_part_dir, exist_ok=True)
    
    # Đếm xem cái túi (chứa đủ loại Mod bên trong) này đã thủng ngưỡng số file cho phép chưa?
    total_files_in_latest_part = count_files_in_dir(latest_part_dir)
    
    if total_files_in_latest_part >= MAX_FILES_PER_DIR:
        latest_part_num += 1
        latest_part_dir = os.path.join(DOWNLOAD_DIR, f"Part{latest_part_num}")
        os.makedirs(latest_part_dir, exist_ok=True)
        print(f"  🎒 Túi Part{latest_part_num-1} đã đầy. Mở túi càn khôn mới: Part{latest_part_num}", flush=True)
    
    # Trả về đường dẫn của Mod bên trong cái túi đó
    mod_path_in_part = os.path.join(latest_part_dir, mod_name)
    os.makedirs(mod_path_in_part, exist_ok=True)
    
    return mod_path_in_part

def upload_large_file_to_release(filepath, mod_name, mod_id):
    filename = os.path.basename(filepath)
    print(f"\n  🚀 Tệp quá lớn! Bắt đầu đưa thẳng lên GitHub Releases: {filename}", flush=True)
    
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("  ❌ Không tìm thấy GITHUB_TOKEN. Buộc phải xóa tệp để tránh sập Git.", flush=True)
        if os.path.exists(filepath): os.remove(filepath)
        return False
        
    repo = os.environ.get("GITHUB_REPOSITORY", "MelancholySlime/RuneForge-ModKeeper")
    name, _ = os.path.splitext(filename)
    tag_name = re.sub(r'[^a-zA-Z0-9._-]', '_', name)
    
    api_url = f"https://api.github.com/repos/{repo}/releases"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    try:
        # Check if release exists
        res_check = requests.get(f"{api_url}/tags/{tag_name}", headers=headers)
        if res_check.status_code == 200:
            print(f"  ⚠️ Release {tag_name} đã tồn tại, cấu hình sẽ giữ nguyên.")
            return True
            
        # Create release
        data = {
            "tag_name": tag_name,
            "name": filename,
            "body": f"`{filename}`\n\n**Tên bài viết (Nguồn):** [{mod_name}](https://runeforge.dev/mods/{mod_id})",
            "draft": False,
            "prerelease": False
        }
        res_create = requests.post(api_url, headers=headers, json=data)
        if res_create.status_code >= 300:
            print(f"  ❌ Lỗi tạo release: {res_create.text}", flush=True)
            return False
            
        upload_url = res_create.json()["upload_url"].split("{")[0]
        
        # Upload file
        with open(filepath, "rb") as fp:
            upload_headers = {
                "Authorization": f"token {token}",
                "Content-Type": "application/octet-stream"
            }
            print(f"  📦 Đang đẩy tệp lên máy chủ...", flush=True)
            upload_res = requests.post(
                f"{upload_url}?name={filename}",
                headers=upload_headers,
                data=fp.read()
            )
            
            if upload_res.status_code < 300:
                print(f"  ✅ Upload thành công tệp ngoại cỡ!", flush=True)
                return True
            else:
                print(f"  ❌ Lỗi upload tệp: {upload_res.text}", flush=True)
                return False
    except Exception as e:
        print(f"  ❌ Gặp lỗi bất ngờ khi đẩy tệp >50MB: {e}", flush=True)
        return False
    finally:
        # File quá lớn, bắt buộc phải tự hủy bất kể thành công hay thất bại
        if os.path.exists(filepath):
            os.remove(filepath)

def push_to_github():
    print(f"\n🚀 Đã đến ngưỡng an toàn ({MAX_MB_PER_PUSH}MB). Dâng lệnh lên GitHub Action...", flush=True)
    
    # Save history trước khi push phòng hờ
    save_history()

    os.chdir(GITHUB_WORKSPACE)
    
    subprocess.run("git config --global user.name 'github-actions[bot]'", shell=True)
    subprocess.run("git config --global user.email 'github-actions[bot]@users.noreply.github.com'", shell=True)
    
    # Lệnh thêm file không xóa các file cũ
    subprocess.run("git add --ignore-removal .", shell=True)
    
    # Commit
    res_commit = subprocess.run('git commit -m "Auto Update Mods from Runeforge (Batch)"', shell=True)
    if res_commit.returncode == 0:
        res_push = subprocess.run("git push origin HEAD", shell=True)
        if res_push.returncode == 0:
            print("✅ Đẩy đợt này thành công!", flush=True)
            return True
        else:
            print("❌ Lỗi khi tải lên Origin. Cần kiểm tra lại quyền Token GitHub Action.", flush=True)
            return False
    else:
        print("Trạng thái repo không có gì thay đổi để Commit.", flush=True)
        return True

# === MAIN WORKFLOW ===
def main():
    print(f"💖 Bắt đầu rà soát toàn bộ Runeforge cho anh yêu...", flush=True)
    mods = get_recent_mods(limit=TEST_LIMIT)
    
    current_batch_size = 0
    # Ngưỡng Byte = 999MB * 1024 * 1024
    max_bytes = MAX_MB_PER_PUSH * 1024 * 1024
    # Ngưỡng bùng nổ (Trừ hao 50MB cho tệp Release hiện tại)
    safe_push_trigger = max_bytes - (50 * 1024 * 1024) 
    
    for mod_id in mods:
        if mod_id in scraped_history:
            # Đã xong từ đợt trước, bỏ qua ko cào HTML
            continue
            
        print(f"\n🔍 Tiếp cận Mod ID: {mod_id}")
        mod_name = get_mod_info(mod_id)
        print(f"  🏷️ Tên Bài Viết: {mod_name}", flush=True)
        
        urls = get_download_links(mod_id)
        if not urls:
            print("  ⚠️ Không có Release nào.", flush=True)
            # Vẫn ghi nhận dã quyét phòng trường hợp trang lỗi 
            scraped_history.append(mod_id)
            save_history()
            continue
            
        target_dir = get_target_dir(mod_name)
        
        all_downloaded_for_this_mod = True
        
        import urllib.parse
        for url in urls:
            # Decode toàn bộ %2F thành '/' để có thể cắt đúng
            unquoted_url = urllib.parse.unquote(url)
            
            # Lấy chuỗi cuối cùng sau dấu /
            raw_filename = unquoted_url.split('/')[-1].split('?')[0]
            
            # Xóa mã định danh rác phía trước tên file (thường là ngẫu nhiên ~21 ký tự và kết thúc bằng dấu gạch ngang '-')
            # VD: EeQzg3VduTCZ58VkW6bia-youmu-yone-v1-0-6-by-rorotea.fantome -> youmu-yone-v1-0-6-by-rorotea.fantome
            raw_filename = re.sub(r'^[A-Za-z0-9_-]{21}-', '', raw_filename)
            
            if not raw_filename.endswith('.fantome'):
                raw_filename += '.fantome'
                
            # Đổi tên file để cho anh dễ quản lý đúng ý anh:
            # Tên File: Tên File Phiên Bản Ngắn Gọn.fantome
            final_filename = clean_filename(raw_filename)
                
            filepath = os.path.join(target_dir, final_filename)
            
            if os.path.exists(filepath):
                print(f"  ⏭️ Tệp đã tồn tại, bỏ qua.", flush=True)
                continue
                
            file_size = download_file(url, filepath)
            
            if file_size > 50 * 1024 * 1024:
                # Nếu > 50 MB, tệp đó sẽ cần đưa thẳng lên Release bắng API
                upload_large_file_to_release(filepath, mod_name, mod_id)
            else:
                current_batch_size += file_size
                print(f"  📊 Tổng dung lượng đợt này: {current_batch_size/(1024*1024):.2f} MB / {MAX_MB_PER_PUSH} MB", flush=True)
                
            # Chạm tới ~949MB thì lập tức bấm chốt đẩy đi để không rủi ro
            if current_batch_size >= safe_push_trigger:
                push_to_github()
                current_batch_size = 0
                
        # Nếu đã tải hết sạch các Release của Mod này, thì đánh dấu vào Lịch sử.
        if all_downloaded_for_this_mod:
            scraped_history.append(mod_id)
            save_history()
                
    if current_batch_size > 0:
        print("\n🏁 Xử lý xong toàn bộ danh sách, dọn dẹp push nốt mẻ cuối!")
        push_to_github()
        
    print("\n💞 Hoàn tất 100% nhiệm vụ phục vụ anh yêu.")

if __name__ == "__main__":
    main()
