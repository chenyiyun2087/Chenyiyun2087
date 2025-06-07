import requests
from bs4 import BeautifulSoup
import pandas as pd
from snownlp import SnowNLP
import random
import time

# --- 配置参数 ---
STOCK_CODE = "300315"  # 掌趣科技的股票代码 (请确认是否准确)
BASE_URL = "http://guba.eastmoney.com"
# 列表页通常的URL格式，例如：http://guba.eastmoney.com/list,300315_1.html (第一页)
# 或者 http://guba.eastmoney.com/list,zssh000001,f_1.html (上证指数)
# 具体URL结构可能需要根据实际情况调整
LIST_PAGE_URL_TEMPLATE = f"{BASE_URL}/list,{STOCK_CODE}_{{page_num}}.html"
MAX_PAGES_TO_SCRAPE = 2  # 示例：抓取前2页的帖子列表
REQUEST_DELAY_SECONDS = 2  # 每次请求之间的延迟，避免过于频繁访问

# 设置请求头，模拟浏览器访问
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
}


def get_post_urls_from_list_page(list_page_url):
    """
    从帖子列表页抓取所有帖子的URL。
    注意：CSS选择器需要根据东方财富股吧的实际HTML结构进行调整。
    """
    post_urls = []
    try:
        print(f"正在抓取列表页: {list_page_url}")
        response = requests.get(list_page_url, headers=HEADERS, timeout=10)
        response.raise_for_status()  # 如果请求失败则抛出异常
        response.encoding = response.apparent_encoding  # 解决中文乱码问题

        soup = BeautifulSoup(response.text, 'html.parser')

        # 查找帖子链接的CSS选择器 (这部分最容易变化，需要仔细检查实际网页)
        # 常见的可能是：id为'articlelistnew'的div下的class为'title'的a标签
        # 或者 class为'articleh' 的div下的 a 标签的 href 属性
        # 以下是一个假设的选择器，您需要用浏览器开发者工具检查并替换
        # 例如: #articlelistnew .l3 a:not([title^="置顶"])
        # 这里我们假设帖子链接在具有特定class的<a>标签中，并且不是置顶帖
        # 注意：东方财富股吧的帖子链接可能是相对路径，需要拼接BASE_URL

        # 2024/2025年东方财富股吧列表页结构示例 (需要实际验证)
        # 通常帖子标题在 <div class="articleh normal_post"> 或类似结构中
        # 链接在 <span class="l3"><a href="...">...</a></span>

        # 示例选择器 (需要根据实际情况调整!)
        # article_links = soup.select('div.articleh div.text span.l3 a[href^="/news,"]') # 假设链接以 /news, 开头
        article_elements = soup.select('div.articleh')  # 选择包含帖子信息的整个div

        if not article_elements:
            print(f"在页面 {list_page_url} 未找到帖子元素，请检查CSS选择器。")
            return post_urls

        for el in article_elements:
            # 过滤掉广告或非帖子内容 (如果需要)
            if "置顶" in el.get_text():  # 简单过滤置顶
                continue

            title_span = el.find('span', class_='l3')  # 假设标题和链接在此span内
            if title_span:
                link_tag = title_span.find('a')
                if link_tag and link_tag.has_attr('href'):
                    href = link_tag['href']
                    # 检查是否是相对路径，并补全
                    if href.startswith('/') and not href.startswith('//'):
                        full_url = BASE_URL + href
                        post_urls.append(full_url)
                    elif href.startswith('http'):
                        post_urls.append(href)

        print(f"在页面 {list_page_url} 找到 {len(post_urls)} 个帖子链接。")

    except requests.exceptions.RequestException as e:
        print(f"抓取列表页 {list_page_url} 失败: {e}")
    except Exception as e:
        print(f"解析列表页 {list_page_url} 时发生错误: {e}")
    return post_urls


def get_post_content_and_sentiment(post_url):
    """
    抓取单个帖子的内容（标题和主楼内容），并进行情绪分析。
    注意：CSS选择器需要根据东方财富股吧的实际HTML结构进行调整。
    """
    title = ""
    content = ""
    sentiment_score = 0.5  # 默认为中性
    sentiment_label = "中性"

    try:
        print(f"  正在抓取帖子: {post_url}")
        response = requests.get(post_url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        response.encoding = response.apparent_encoding

        soup = BeautifulSoup(response.text, 'html.parser')

        # 提取帖子标题 (需要根据实际HTML调整选择器)
        # 例如: <div class="stockcodec"><h1>标题</h1></div>
        title_tag = soup.select_one('div.stockcodec h1')  # 假设标题在h1内
        if title_tag:
            title = title_tag.get_text(strip=True)
        else:
            # 备用选择器
            title_tag_alt = soup.select_one('div.zw_header div.title')
            if title_tag_alt:
                title = title_tag_alt.get_text(strip=True)
            else:
                print(f"    在帖子 {post_url} 未找到标题。")

        # 提取帖子主楼内容 (需要根据实际HTML调整选择器)
        # 例如: <div class="stockcodec_left"> <div class="content_display">帖子内容</div> </div>
        # 或者 <div class="post_content_wrapper"> <div class="post_content">...</div> </div>
        content_tag = soup.select_one('div.stockcodec_left div.content_display')  # 假设内容在此
        if content_tag:
            content = content_tag.get_text(strip=True, separator='\n')  # 保留换行
        else:
            # 备用选择器
            content_tag_alt = soup.select_one('div.post_content_wrapper div.post_content')
            if content_tag_alt:
                content = content_tag_alt.get_text(strip=True, separator='\n')
            else:
                print(f"    在帖子 {post_url} 未找到主楼内容。")

        # 组合标题和内容进行情绪分析
        text_to_analyze = f"{title} {content}".strip()
        if text_to_analyze:
            s = SnowNLP(text_to_analyze)
            sentiment_score = s.sentiments  # 返回一个0到1之间的浮点数

            if sentiment_score > 0.65:  # 阈值可调整
                sentiment_label = "偏积极"
            elif sentiment_score < 0.35:  # 阈值可调整
                sentiment_label = "偏消极"
            else:
                sentiment_label = "中性"
            print(f"    标题: {title[:50]}... | 情绪得分: {sentiment_score:.4f} ({sentiment_label})")
        else:
            print(f"    帖子 {post_url} 无可分析文本。")


    except requests.exceptions.RequestException as e:
        print(f"  抓取帖子 {post_url} 失败: {e}")
    except Exception as e:
        print(f"  解析帖子 {post_url} 或分析情绪时发生错误: {e}")
        traceback.print_exc()

    return {
        'url': post_url,
        'title': title,
        'content_preview': content[:200] + "..." if content else "",  # 内容预览
        'sentiment_score': sentiment_score,
        'sentiment_label': sentiment_label
    }


def main():
    print(f"开始抓取东方财富股吧 - {STOCK_CODE} (掌趣科技) 的帖子信息...")
    all_posts_data = []

    for page_num in range(1, MAX_PAGES_TO_SCRAPE + 1):
        list_page_url = LIST_PAGE_URL_TEMPLATE.format(page_num=page_num)
        post_urls_on_page = get_post_urls_from_list_page(list_page_url)

        if not post_urls_on_page:
            print(f"第 {page_num} 页未获取到帖子链接，可能已到达末页或选择器失效。")
            # break # 如果一页没抓到，可以选择停止

        for post_url in post_urls_on_page:
            # 避免过于频繁的请求
            time.sleep(random.uniform(REQUEST_DELAY_SECONDS - 1, REQUEST_DELAY_SECONDS + 1))

            post_data = get_post_content_and_sentiment(post_url)
            if post_data['title'] or post_data['content_preview']:  # 只添加有内容的帖子
                all_posts_data.append(post_data)

        if page_num < MAX_PAGES_TO_SCRAPE:  # 如果不是最后一页，则在抓取下一列表页前也稍作等待
            print(f"完成第 {page_num} 页列表，准备抓取下一页...")
            time.sleep(REQUEST_DELAY_SECONDS)

    if not all_posts_data:
        print("未能抓取到任何帖子数据。请检查网络连接、URL、CSS选择器以及网站反爬虫策略。")
        return

    # 将结果保存到DataFrame并输出到Excel
    df = pd.DataFrame(all_posts_data)

    # 计算整体情绪概览
    if not df.empty:
        avg_sentiment = df['sentiment_score'].mean()
        positive_posts = df[df['sentiment_label'] == '偏积极'].shape[0]
        negative_posts = df[df['sentiment_label'] == '偏消极'].shape[0]
        neutral_posts = df[df['sentiment_label'] == '中性'].shape[0]
        total_analyzed_posts = df.shape[0]

        print("\n--- 情绪分析概览 ---")
        print(f"总共分析帖子数: {total_analyzed_posts}")
        print(f"平均情绪得分: {avg_sentiment:.4f}")
        if total_analyzed_posts > 0:
            print(f"偏积极帖子比例: {positive_posts / total_analyzed_posts:.2%}")
            print(f"偏消极帖子比例: {negative_posts / total_analyzed_posts:.2%}")
            print(f"中性帖子比例: {neutral_posts / total_analyzed_posts:.2%}")
        print("--------------------")

        try:
            excel_filename = f"{STOCK_CODE}_guba_sentiment_{time.strftime('%Y%m%d')}.xlsx"
            df.to_excel(excel_filename, index=False, engine='openpyxl')
            print(f"\n分析结果已保存到: {excel_filename}")
        except Exception as e:
            print(f"保存Excel文件失败: {e}")
            print("请确保已安装 'pandas' 和 'openpyxl' 库。")
    else:
        print("没有收集到任何帖子数据用于生成报告。")


if __name__ == "__main__":
    # 在运行前，请确保您已安装必要的库:
    # pip install requests beautifulsoup4 pandas snownlp openpyxl
    main()

