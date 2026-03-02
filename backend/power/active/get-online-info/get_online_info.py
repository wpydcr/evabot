import argparse
import json
import os
import urllib.request
import urllib.error

def format_results(data):
    """将 JSON 数据格式化为极简文本，节省 Token"""
    page_items = data.get('pageItems', [])
    if not page_items:
        return "未搜索到相关结果。"
    
    formatted_output = []
    for i, item in enumerate(page_items):
        title = item.get('title', '无标题')
        link = item.get('link', '无链接')
        snippet = item.get('snippet', '')
        
        # 构建基础信息
        block = f"[{i+1}] {title}\nURL: {link}\n摘要: {snippet}"
        
        # 附加用户可能请求的长文本字段
        if item.get('summary'):
            block += f"\n增强摘要: {item['summary']}"
        if item.get('mainText'):
            block += f"\n正文: {item['mainText']}"
        if item.get('markdownText'):
            block += f"\nMarkdown正文: {item['markdownText']}"
        if item.get('richMainBody'):
            block += f"\n富文本正文: {item['richMainBody']}"
            
        formatted_output.append(block)
        
    return "\n---\n".join(formatted_output)

def handle_error(status_code, error_body):
    """解析错误信息并返回易于理解的提示"""
    try:
        err_data = json.loads(error_body)
        code = err_data.get("Code", "")
        message = err_data.get("Message", "")
    except json.JSONDecodeError:
        code = "Unknown"
        message = error_body

    error_msg = f"API 请求失败 (HTTP {status_code}): {code} - {message}\n"
    
    # 按照文档定义的错误码给出具体排查建议
    if status_code == 404:
        error_msg += "建议: 请检查并确保 ALIYUN_API_KEY 的 AccessKey/Secret 正确。"
    elif status_code == 403:
        if "NotActivate" in code:
            error_msg += "建议: 请开通 AI 搜索服务。"
        elif "Arrears" in code:
            error_msg += "建议: 账户金额不足，请充值。"
        elif "NotAuthorised" in code:
            error_msg += "建议: 请为子账号授权 AliyunIQSFullAccess 权限。"
        elif "TestUserPeriodExpired" in code:
            error_msg += "建议: 测试已到期(15天有效)，请转为正式用户。"
    elif status_code == 429:
        if "Throttling.User" in code:
            error_msg += "建议: 超出限流规格，请联系客户经理进行升配。"
        elif "TestUserQueryPerDayExceeded" in code:
            error_msg += "建议: 测试超出日限额(1000次/天)，请转为正式用户。"

    return error_msg

def main():
    parser = argparse.ArgumentParser(description="Aliyun IQS Search Tool")
    parser.add_argument('--data', type=str, required=True, help='JSON 格式的请求参数字符串')
    args = parser.parse_args()

    # 1. 检查环境变量
    api_key = os.environ.get('ALIYUN_API_KEY')
    if not api_key:
        print("ValueError: 环境变量 ALIYUN_API_KEY 未设置")
        return

    # 2. 解析传入的 JSON 数据
    try:
        payload = json.loads(args.data)
    except json.JSONDecodeError:
        print("ValueError: --data 传入的参数不是有效的 JSON 字符串")
        return

    # 3. 发送请求
    url = "https://cloud-iqs.aliyuncs.com/search/unified"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    req = urllib.request.Request(
        url, 
        data=json.dumps(payload).encode('utf-8'), 
        headers=headers, 
        method='POST'
    )

    try:
        with urllib.request.urlopen(req) as response:
            response_data = json.loads(response.read().decode('utf-8'))
            compact_result = format_results(response_data)
            print(compact_result)
            
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        print("RuntimeError:", handle_error(e.code, error_body))
    except Exception as e:
        print(f"RuntimeError: 发生未知异常: {str(e)}")

if __name__ == "__main__":
    main()