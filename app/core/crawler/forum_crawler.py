from bs4 import BeautifulSoup
import time

# 使用本地demo文件路径
LIST_DEMO_PATH = "/data/5e-translator/app/core/crawler/list-demo.html"
PAGE_DEMO_PATH = "/data/5e-translator/app/core/crawler/page-demo.html"

# 基础URL
BASE_URL = "https://www.goddessfantasy.net/bbs"

# 测试模式：True使用本地demo，False从网络获取
TEST_MODE = False

# 列表页URL参数
LIST_URL = "https://www.goddessfantasy.net/bbs/index.php?board=2250.0"

def get_topic_links(list_url):
    """从网络获取所有帖子链接"""
    try:
        import requests
        # 包含用户提供的Cookie
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Cookie": "SMFCookieElle=%7B%220%22%3A181237%2C%221%22%3A%22415536f3f154c5cea99e2d57044619d641fce2a870c91ddf40f0cf90baecd7091b567c63b6c262c840e7b99680d31e5f9913d52d52a8497e9859b5f19dcb3bb4%22%2C%222%22%3A1945740005%2C%223%22%3A%22www.goddessfantasy.net%22%2C%224%22%3A%22%5C%2Fbbs%5C%2F%22%7D; PHPSESSID=72fgs65lrteiu3ge2t4l23ocnp; cf_clearance=sx4dv0cYP8FGzEpBNGnssa9xdVius_iqpXLdGh.gzBo-1768481605-1.2.1.1-lpBUKwodQrJRBt7nwEd8UtW7MTHhmQCLIS7dxC0TzX4oavkeOF3e3VG0Jv4ZbUPRn_uXkFSvRY5Wu_Mz20hfPfCOw0gxFbQnOVBz2rIm1.d8Fw6jJ42tAQt7CFUORJMc0LDa1gsy1kefZdLETT5AWc6L1yUiL34FTPO8Hgng897YF1g1X4WG79lnXcWZR0hFQI3hY5vOkiyi0_1NkAeES7dHQ2iQUUeEhlBJukBlKPE"
        }
        response = requests.get(list_url, headers=headers)
        response.encoding = "UTF-8"
        soup = BeautifulSoup(response.text, "html.parser")
        
        # 找到所有帖子容器
        topic_container = soup.find("div", id="topic_container")
        if not topic_container:
            print(f"未找到帖子容器: {list_url}")
            return []
        
        topic_links = []
        # 遍历每个帖子项
        for windowbg in topic_container.find_all("div", class_="windowbg"):
            # 找到帖子标题链接
            title_div = windowbg.find("div", class_="message_index_title")
            if title_div:
                link = title_div.find("a")
                if link and "href" in link.attrs:
                    topic_links.append(link["href"])
        
        return topic_links
    except Exception as e:
        print(f"获取帖子链接失败: {e}")
        return []

def get_topic_content(topic_url):
    """获取帖子内容，找到帖主发的所有内容"""
    try:
        if TEST_MODE:
            # 使用本地demo文件
            with open(PAGE_DEMO_PATH, "r", encoding="UTF-8") as f:
                content = f.read()
        else:
            # 从网络获取实际内容
            import requests
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Cookie": "SMFCookieElle=%7B%220%22%3A181237%2C%221%22%3A%22415536f3f154c5cea99e2d57044619d641fce2a870c91ddf40f0cf90baecd7091b567c63b6c262c840e7b99680d31e5f9913d52d52a8497e9859b5f19dcb3bb4%22%2C%222%22%3A1945740005%2C%223%22%3A%22www.goddessfantasy.net%22%2C%224%22%3A%22%5C%2Fbbs%5C%2F%22%7D; PHPSESSID=72fgs65lrteiu3ge2t4l23ocnp; cf_clearance=sx4dv0cYP8FGzEpBNGnssa9xdVius_iqpXLdGh.gzBo-1768481605-1.2.1.1-lpBUKwodQrJRBt7nwEd8UtW7MTHhmQCLIS7dxC0TzX4oavkeOF3e3VG0Jv4ZbUPRn_uXkFSvRY5Wu_Mz20hfPfCOw0gxFbQnOVBz2rIm1.d8Fw6jJ42tAQt7CFUORJMc0LDa1gsy1kefZdLETT5AWc6L1yUiL34FTPO8Hgng897YF1g1X4WG79lnXcWZR0hFQI3hY5vOkiyi0_1NkAeES7dHQ2iQUUeEhlBJukBlKPE"
            }
            response = requests.get(topic_url, headers=headers)
            response.encoding = "UTF-8"
            content = response.text
        
        soup = BeautifulSoup(content, "html.parser")
        
        # 找到所有帖子容器
        all_posts = soup.find_all("div", class_="windowbg")
        if not all_posts:
            print(f"未找到帖子内容: {topic_url}")
            return None
        
        # 找到帖主的用户名
        # 假设第一个帖子的作者就是帖主
        first_post = all_posts[0]
        poster_info = first_post.find("div", class_="poster")
        if not poster_info:
            print(f"未找到帖主信息: {topic_url}")
            return None
        
        poster_name = poster_info.find("a")
        if not poster_name:
            print(f"未找到帖主用户名: {topic_url}")
            return None
        
        poster_username = poster_name.text.strip()
        print(f"帖主用户名: {poster_username}")
        
        # 收集帖主发的所有内容
        poster_content = []
        
        for post in all_posts:
            # 获取当前帖子的作者
            current_poster = post.find("div", class_="poster")
            if not current_poster:
                continue
            
            current_poster_name = current_poster.find("a")
            if not current_poster_name:
                continue
            
            current_username = current_poster_name.text.strip()
            
            # 如果是帖主发的帖子，提取内容
            if current_username == poster_username:
                post_content = post.find("div", class_="inner")
                if post_content:
                    # 移除不必要的元素
                    for element in post_content.find_all(["script", "style"]):
                        element.decompose()
                    
                    # 获取纯文本内容
                    text = post_content.get_text(separator="\n", strip=True)
                    poster_content.append(text)
        
        # 将所有帖主内容合并
        if poster_content:
            return {
                "url": topic_url,
                "poster": poster_username,
                "content": "\n\n--- 帖主新回复 ---\n\n".join(poster_content)
            }
        else:
            print(f"未找到帖主发的内容: {topic_url}")
            return None
    except Exception as e:
        print(f"获取帖子内容失败: {e}")
        return None

def main():
    """主函数"""
    print("开始爬取论坛帖子...")
    
    all_topic_links = []
    visited_links = set()  # 用于记录已访问的帖子链接
    page = 0
    has_more = True
    
    # 分页爬取所有帖子链接
    while has_more:
        # 构建分页URL
        if page == 0:
            current_url = LIST_URL
        else:
            # 第二页是board=2138.30，第三页是board=2138.60，以此类推
            current_url = LIST_URL.replace(".0", f".{page * 30}")
        
        print(f"\n正在爬取页面: {current_url}")
        
        # 获取当前页面的帖子链接
        topic_links = get_topic_links(current_url)
        print(f"当前页找到 {len(topic_links)} 个帖子")
        
        # 检查是否有重复链接
        has_duplicates = False
        new_links = []
        
        for link in topic_links:
            if link in visited_links:
                has_duplicates = True
            else:
                visited_links.add(link)
                new_links.append(link)
        
        # 添加新链接到总列表
        all_topic_links.extend(new_links)
        print(f"当前页新增 {len(new_links)} 个帖子")
        
        # 判断是否还有更多页面
        if has_duplicates or len(topic_links) == 0:
            has_more = False
            print("发现重复帖子或当前页无帖子，停止爬取")
        else:
            page += 1
        
        # 休眠一下，避免请求过快
        time.sleep(1)
    
    print(f"\n共找到 {len(all_topic_links)} 个帖子")
    
    # 准备输出文件
    output_file = "/data/5e-translator/app/core/crawler/forum_posts2129.txt"
    print(f"\n将结果输出到文件: {output_file}")
    
    # 打开文件，准备写入
    with open(output_file, "w", encoding="UTF-8") as f:
        # 遍历每个帖子链接，获取内容
        for i, link in enumerate(all_topic_links, 1):
            print(f"\n正在处理第 {i} 个帖子: {link}")
            post_data = get_topic_content(link)
            
            if post_data:
                # 输出到控制台
                print(f"帖子内容:\n{post_data['content'][:200]}...")  # 只显示前200字符
                
                # 写入文件
                f.write(f"========== 帖子 {i} ==========\n")
                f.write(f"URL: {post_data['url']}\n")
                f.write(f"帖主: {post_data['poster']}\n")
                f.write(f"内容:\n{post_data['content']}\n")
                f.write("\n" + "="*50 + "\n\n")
            else:
                print("未获取到帖子内容")
                f.write(f"========== 帖子 {i} ==========\n")
                f.write(f"URL: {link}\n")
                f.write("错误: 未获取到帖子内容\n")
                f.write("\n" + "="*50 + "\n\n")
            
            # 休眠一下，避免请求过快
            time.sleep(1)
    
    print(f"\n爬取完成！结果已输出到文件: {output_file}")

if __name__ == "__main__":
    main()
