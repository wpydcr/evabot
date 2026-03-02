import os
import io
import shutil
import zipfile
import concurrent.futures
from typing import Dict, List, Optional
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import urllib.parse
import requests
import yaml
import sys
import json
import argparse

_repo_tree_cache = {}

def extract_frontmatter(content: str) -> dict:
    """使用 pyyaml 解析 Markdown 的 YAML Frontmatter"""
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            try:
                return yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                pass
    return {}

def _get_repo_tree(source: str):
    """单次获取并缓存整个仓库的文件树结构，极速秒开"""
    if source in _repo_tree_cache:
        return _repo_tree_cache[source]
        
    headers = {}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    for branch in ["main", "master"]:
        tree_url = f"https://api.github.com/repos/{source}/git/trees/{branch}?recursive=1"
        try:
            resp = requests.get(tree_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                tree = resp.json().get('tree', [])
                _repo_tree_cache[source] = (branch, tree)
                return branch, tree
        except requests.RequestException:
            continue
            
    _repo_tree_cache[source] = (None, [])
    return None, []

def download_file(url: str, dest_path: str):
    """直接下载远端源文件到本地路径 (适用于单个文件)"""
    try:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            with open(dest_path, 'wb') as f:
                f.write(resp.content)
            return True, resp.text
    except Exception:
        pass
    return False, ""

def download_and_extract_zip(url: str, dest_dir: str) -> bool:
    """基于内存下载并解压ZIP，自动拍平多余层级"""
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            os.makedirs(dest_dir, exist_ok=True)
            # 内存中解压，避免磁盘 I/O 开销
            with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                z.extractall(dest_dir)
            
            # 自动拍平逻辑：如果解压后发现只有1个孤零零的文件夹，把里面的东西全提出来
            extracted_items = os.listdir(dest_dir)
            if len(extracted_items) == 1:
                single_item = os.path.join(dest_dir, extracted_items[0])
                if os.path.isdir(single_item):
                    for item in os.listdir(single_item):
                        shutil.move(os.path.join(single_item, item), dest_dir)
                    os.rmdir(single_item)
            # 如过存在_meta.json文件，删除
            meta_path = os.path.join(dest_dir, "_meta.json")
            if os.path.exists(meta_path):
                os.remove(meta_path)
            return True
    except Exception as e:
        pass
    return False


def find_skills_clawhub(query: str, save_dir: str) -> List[Dict]:
    """使用 Playwright 抓取 ClawHub 并利用其开放API直接下载解压ZIP，带有完整的异常隔离"""
    encoded_query = urllib.parse.quote(query)
    url = f"https://clawhub.ai/skills?sort=downloads&q={encoded_query}&nonSuspicious=true"
    
    if not os.path.exists(save_dir):
        os.makedirs(save_dir, exist_ok=True)
        
    results = []
    
    # ================= 第1层保护：全局抓取级别隔离 (防 Playwright 崩溃) =================
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # ================= 第2层保护：页面加载级别隔离 (防网络超时或没有搜索结果) =================
            try:
                page.goto(url, wait_until="networkidle")
                # 如果搜不到结果，可能会超时找不到 .skills-row，这是正常的
                page.wait_for_selector(".skills-row", timeout=10000)
                
                html_content = page.content()
                soup = BeautifulSoup(html_content, 'html.parser')
                skill_rows = soup.find_all('a', class_='skills-row')
                get_count = 0
                for row in skill_rows:
                    if get_count >= 10:
                        break
                    # ================= 第3层保护：单个技能级别隔离 (防解析报错或 ZIP 下载失败) =================
                    try:
                        href = row.get('href', '')
                        source = href.replace('https://clawhub.ai/', '').strip()
                        if source.startswith('/'):
                            source = source[1:]
                        
                        title_div = row.find('div', class_='skills-row-title')
                        slug = "unknown-slug"
                        
                        if title_div:                            
                            slug_span = title_div.find('span', class_='skills-row-slug')
                            if slug_span:
                                slug = slug_span.text.strip().lstrip('/')
                        
                        summary_div = row.find('div', class_='skills-row-summary')
                        description = summary_div.text.strip() if summary_div else "暂无描述"
                        
                        metrics_div = row.find('div', class_='skills-row-metrics')
                        installs = "0"
                        if metrics_div:
                            spans = metrics_div.find_all('span')
                            if spans:
                                installs = spans[0].text.strip()
                                
                        # ---------------- 下载逻辑 ----------------
                        dest_folder = os.path.join(save_dir, slug)
                        zip_url = f"https://wry-manatee-359.convex.site/api/v1/download?slug={slug}"
                        
                        if download_and_extract_zip(zip_url, dest_folder):
                            get_count += 1
                        else:
                            continue
                        # ------------------------------------------

                        results.append({
                            "name": slug,
                            "source": source,
                            "description": description,
                            "installs": installs,
                            "platform": "ClawHub",
                            "local_path": dest_folder
                        })
                    except Exception as inner_e:
                        print(f"  -> [ClawHub] 解析或下载技能 '{slug}' 时发生错误: {inner_e}，已跳过")
                        continue
                        
            except Exception as page_e:
                # 抓取不到元素通常是因为没有搜索结果，属于正常业务逻辑分支，打印提示即可
                print(f"  -> [ClawHub] 页面加载或等待元素超时 (可能无搜索结果或网络问题)")
            finally:
                browser.close()
                
    except Exception as global_e:
        print(f"  -> [ClawHub] Playwright 启动或执行发生全局错误: {global_e}")
        
    return results

def find_skills_skillsh(query: str, save_dir: str) -> List[Dict]:
    """通过 Github Trees API 定位技能文件，并并发提取下载"""
    api_url = "https://skills.sh/api/search"
    params = {"q": query, "limit": 10}
    valid_results = []
    
    if not os.path.exists(save_dir):
        os.makedirs(save_dir, exist_ok=True)
        
    # ================= 第1层保护：API 请求 =================
    try:
        response = requests.get(api_url, params=params, timeout=10)
        response.raise_for_status()
        skills_data = response.json().get("skills", [])
    except requests.RequestException as e:
        print(f"从 skills.sh 的 api 获取数据失败: {str(e)}")
        # API 失败直接返回空列表，不影响外部程序的执行
        return valid_results
        
    # 相同仓库分组，防止重复拉取文件树
    skills_by_source = {}
    for skill in skills_data:
        source = skill.get("source")
        if not source or source.count('/') != 1:
            continue
        skills_by_source.setdefault(source, []).append(skill)
        
    for source, target_skills in skills_by_source.items():
        # ================= 第2层保护：仓库级别隔离 =================
        try:
            branch, tree = _get_repo_tree(source)
            if not branch or not tree:
                print(f"  -> [skills.sh] 无法获取仓库文件树: {source}，已跳过")
                continue
                
            skill_md_paths = [item['path'] for item in tree if item['type'] == 'blob' and item['path'].endswith('SKILL.md')]
            
            for target_skill in target_skills:
                # ================= 第3层保护：单个技能级别隔离 =================
                try:
                    target_name = target_skill.get("name", "Unknown")
                    target_slug = target_skill.get("id", "")
                    installs = target_skill.get("installs", 0)
                    
                    matched_skill_md = None
                    for p in skill_md_paths:
                        if f"/{target_slug}/" in p or p.startswith(f"{target_slug}/") or \
                           f"/{target_name}/" in p or p.startswith(f"{target_name}/"):
                            matched_skill_md = p
                            break
                    if not matched_skill_md and skill_md_paths:
                        matched_skill_md = skill_md_paths[0] 
                        
                    if matched_skill_md:
                        folder_path = os.path.dirname(matched_skill_md)
                        
                        # 提取纯净技能名
                        skill_folder_name = target_slug.split('/')[-1] if target_slug else target_name.replace('/', '-')
                        dest_folder = os.path.join(save_dir, skill_folder_name)
                        
                        files_to_download = []
                        for item in tree:
                            if item['type'] == 'blob':
                                if folder_path == "":
                                    if '/' not in item['path']:
                                        files_to_download.append(item['path'])
                                elif item['path'].startswith(folder_path + '/'):
                                    files_to_download.append(item['path'])
                                    
                        
                        description = "暂无描述"
                        
                        def do_download(file_path):
                            raw_url = f"https://raw.githubusercontent.com/{source}/{branch}/{file_path}"
                            if folder_path == "":
                                rel_path = file_path
                            else:
                                rel_path = file_path[len(folder_path)+1:]
                            
                            local_dest = os.path.join(dest_folder, rel_path)
                            success, text_content = download_file(raw_url, local_dest)
                            return file_path, success, text_content

                        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as dl_executor:
                            futures = [dl_executor.submit(do_download, fp) for fp in files_to_download]
                            for future in concurrent.futures.as_completed(futures):
                                fp, succ, text = future.result()
                                if succ and fp.endswith('SKILL.md'):
                                    meta = extract_frontmatter(text)
                                    description = meta.get('description', description)
                                    
                        valid_results.append({
                            "name": target_name,
                            "source": source,
                            "description": str(description).strip(),
                            "installs": installs,
                            "platform": "skills.sh",
                            "local_path": dest_folder
                        })
                except Exception as inner_e:
                    print(f"  -> [skills.sh] 处理技能 '{target_skill.get('name', 'Unknown')}' 时发生错误: {inner_e}，已跳过")
                    continue
                    
        except Exception as e:
            print(f"  -> [skills.sh] 处理仓库 '{source}' 时发生全局错误: {e}，已跳过")
            continue
            
    return valid_results

def execute(query: str, save_dir: str) -> List[Dict]:
    """
    主执行函数：
    1. 完美并行，谁先完成谁合并
    2. 全局去重，防止脏数据导致重复下载和展示
    """
    raw_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        # submit 后立即并行开始执行，不互相等待
        futures = [
            executor.submit(find_skills_skillsh, query, save_dir),
            executor.submit(find_skills_clawhub, query, save_dir)
        ]
        
        # as_completed 保证哪个平台的任务先做完，就先拿谁的结果，无需排队
        for future in concurrent.futures.as_completed(futures):
            try:
                raw_results.extend(future.result())
            except Exception as e:
                print(f"获取技能时发生错误: {e}")
    
    # --- 核心修复：全局去重 ---
    # 防止 API 或 网页 返回相同的技能导致列表中出现多个 Robot
    unique_results = []
    seen = set()
    for r in raw_results:
        # 提取关键信息，如果有任何不可见字符，先清理一下
        platform = str(r.get('platform', '')).strip()
        source = str(r.get('source', '')).strip()
        name = str(r.get('name', '')).strip()
        
        # 拼接成唯一指纹，例如：ClawHub_ivangdavila/robot_Robot
        identifier = f"{platform}_{source}_{name}"
        if identifier not in seen:
            seen.add(identifier)
            unique_results.append(r)
            
    return unique_results


if __name__ == "__main__":
    # 1. 设置命令行参数解析
    parser = argparse.ArgumentParser(description="搜索并下载 Skills")
    parser.add_argument("-q", "--query", type=str, required=True, help="搜索关键词")
    parser.add_argument("-d", "--save_dir", type=str, required=True, help="保存目录路径")
    args = parser.parse_args()

    # 2. 将内部的日志 print 重定向到 stderr，防止污染标准输出 (可选，但强烈建议)
    # 这样写的好处是，你不需要去修改上面所有的 print 语句
    original_stdout = sys.stdout
    sys.stdout = sys.stderr 

    try:
        # 执行核心逻辑
        valid_results = execute(args.query, save_dir=args.save_dir)
    except Exception as e:
        # 发生严重错误时，返回空的或带有错误信息的 JSON
        valid_results = {"error": str(e)}
    finally:
        # 3. 恢复标准输出
        sys.stdout = original_stdout

    # 4. 将最终结果以纯 JSON 格式打印到标准输出
    if isinstance(valid_results, dict) and "error" in valid_results:
        print(json.dumps(valid_results, ensure_ascii=True))
        sys.exit(1)
    # 这里的 print 是唯一一个输出到 stdout 的，调用方只会拿到这段 JSON
    keys = ["name", "source", "description", "installs", "platform", "local_path"]
    llm_data = [keys] # 第一行是表头
    # 提取数据
    for item in valid_results:
        row = [
            item.get("name", ""),
            item.get("source", ""),
            item.get("description", ""),
            item.get("installs", 0),
            item.get("platform", "ClawHub"),
            item.get("local_path", "")
        ]
        llm_data.append(row)

    print(json.dumps(llm_data, ensure_ascii=True))
    # print(json.dumps(valid_results, ensure_ascii=True))