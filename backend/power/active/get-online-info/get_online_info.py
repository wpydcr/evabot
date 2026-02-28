import os
import requests
import json

# 从环境变量读取 API Key
api_key = os.environ.get('ALIYUN_API_KEY')
if not api_key:
    raise ValueError("环境变量 'ALIYUN_API_KEY' 未设置。请设置 ALIYUN_API_KEY 环境变量后重试。")

def get_online_info(query, timeRange="NoLimit", category=None, engineType="Generic", 
                    city=None, ip=None, mainText=False, markdownText=False, 
                    richMainBody=False, summary=False, rerankScore=True):
    """
    获取互联网信息
    
    Args:
        query: 搜索问题（必填）
        timeRange: 时间范围，默认 NoLimit
        category: 查询分类，默认 None
        engineType: 引擎类型，默认 Generic
        city: 城市名，默认 None
        ip: 位置IP，默认 None
        mainText: 是否返回长正文，默认 False
        markdownText: 是否返回markdown格式正文，默认 False
        richMainBody: 是否返回富文本全正文，默认 False
        summary: 是否返回增强摘要，默认 False
        rerankScore: 是否进行Rerank并返回得分，默认 True
    
    Returns:
        dict: API 响应结果
    """
    url = "https://cloud-iqs.aliyuncs.com/search/unified"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # 构建请求参数
    data = {
        "query": query
    }
    
    # 添加可选参数
    if timeRange != "NoLimit":
        data["timeRange"] = timeRange
    
    if category:
        data["category"] = category
    
    if engineType != "Generic":
        data["engineType"] = engineType
    
    # locationInfo
    if city or ip:
        data["locationInfo"] = {}
        if city:
            data["locationInfo"]["city"] = city
        if ip:
            data["locationInfo"]["ip"] = ip
    
    # contents
    if mainText or markdownText or richMainBody or summary or not rerankScore:
        data["contents"] = {}
        if mainText:
            data["contents"]["mainText"] = True
        if markdownText:
            data["contents"]["markdownText"] = True
        if richMainBody:
            data["contents"]["richMainBody"] = True
        if summary:
            data["contents"]["summary"] = True
        if not rerankScore:
            data["contents"]["rerankScore"] = False
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"API 请求失败: {e}")

# 示例用法
if __name__ == "__main__":
    # 示例：获取北京天气信息
    try:
        result = get_online_info("北京天气")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"错误: {e}")
