# coding=utf-8
import json
import os
from loguru import logger

from dy_apis.douyin_api import DouyinAPI
from utils.common_util import init
from utils.data_util import handle_work_info, download_work, save_to_xlsx


def safe_download_work(work_info, path, save_choice):
    # download_work 自带重试，重试后仍失败则跳过该作品，避免中断整批爬取
    try:
        download_work(work_info, path, save_choice)
    except Exception as e:
        logger.error(f'作品 {work_info["work_id"]} 下载失败，已跳过: {e}')


class Data_Spider():
    def __init__(self):
        self.douyin_apis = DouyinAPI()

    def spider_work(self, auth, work_url: str, proxies=None):
        """
        爬取一个作品的信息
        :param auth : 用户认证信息
        :param work_url: 作品链接
        :return:
        """
        res_json = self.douyin_apis.get_work_info(auth, work_url)
        data = res_json['aweme_detail']

        work_info = handle_work_info(data)
        logger.info(f'爬取作品信息 {work_url}')
        return work_info

    def spider_some_work(self, auth, works: list, base_path: dict, save_choice: str, excel_name: str = '', proxies=None):
        """
        爬取一些作品的信息
        :param auth: 用户认证信息
        :param works: 作品链接列表
        :param base_path: 保存路径
        :param save_choice: 保存方式 all: 保存所有的信息, media: 保存视频和图片（media-video只下载视频, media-image只下载图片，media都下载）, excel: 保存到excel
        :param excel_name: excel文件名
        :return:
        """
        if (save_choice == 'all' or save_choice == 'excel') and excel_name == '':
            raise ValueError('excel_name 不能为空')
        work_list = []
        for work_url in works:
            work_info = self.spider_work(auth, work_url)
            work_list.append(work_info)
        for work_info in work_list:
            if save_choice == 'all' or 'media' in save_choice:
                safe_download_work(work_info, base_path['media'], save_choice)
        if save_choice == 'all' or save_choice == 'excel':
            file_path = os.path.abspath(os.path.join(base_path['excel'], f'{excel_name}.xlsx'))
            save_to_xlsx(work_list, file_path)


    def spider_user_all_work(self, auth, user_url: str, base_path: dict, save_choice: str, excel_name: str = '', proxies=None):
        """
        爬取一个用户的所有作品
        :param auth: 用户认证信息
        :param user_url: 用户链接
        :param base_path: 保存路径
        :param save_choice: 保存方式 all: 保存所有的信息, media: 保存视频和图片（media-video只下载视频, media-image只下载图片，media都下载）, excel: 保存到excel
        :param excel_name: excel文件名
        :param proxies: 代理
        :return:
        """
        user_info = self.douyin_apis.get_user_info(auth, user_url)
        work_list = self.douyin_apis.get_user_all_work_info(auth, user_url)
        work_info_list = []
        logger.info(f'用户 {user_url} 作品数量: {len(work_list)}')
        if save_choice == 'all' or save_choice == 'excel':
            excel_name = user_url.split('/')[-1].split('?')[0]

        for work_info in work_list:
            work_info['author'].update(user_info['user'])
            work_info = handle_work_info(work_info)
            work_info_list.append(work_info)
            logger.info(f'爬取作品信息 {work_info["work_url"]}')
            if save_choice == 'all' or 'media' in save_choice:
                safe_download_work(work_info, base_path['media'], save_choice)
        if save_choice == 'all' or save_choice == 'excel':
            file_path = os.path.abspath(os.path.join(base_path['excel'], f'{excel_name}.xlsx'))
            save_to_xlsx(work_info_list, file_path)

    def spider_some_search_work(self, auth, query: str, require_num: int, base_path: dict, save_choice: str,  sort_type: str, publish_time: str, filter_duration="", search_range="", content_type="",   excel_name: str = '', proxies=None):
        """
            :param auth: DouyinAuth object.
            :param query: 搜索关键字.
            :param require_num: 搜索结果数量.
            :param base_path: 保存路径.
            :param save_choice: 保存方式 all: 保存所有的信息, media: 保存视频和图片（media-video只下载视频, media-image只下载图片，media都下载）, excel: 保存到excel
            :param sort_type: 排序方式 0 综合排序, 1 最多点赞, 2 最新发布.
            :param publish_time: 发布时间 0 不限, 1 一天内, 7 一周内, 180 半年内.
            :param filter_duration: 视频时长 空字符串 不限, 0-1 一分钟内, 1-5 1-5分钟内, 5-10000 5分钟以上
            :param search_range: 搜索范围 0 不限, 1 最近看过, 2 还未看过, 3 关注的人
            :param content_type: 内容形式 0 不限, 1 视频, 2 图文
            :param excel_name: excel文件名
        """
        work_info_list = []
        work_list = self.douyin_apis.search_some_general_work(auth, query, require_num, sort_type, publish_time, filter_duration, search_range, content_type)
        logger.info(f'搜索关键词 {query} 作品数量: {len(work_list)}')
        if save_choice == 'all' or save_choice == 'excel':
            excel_name = query
        for work_info in work_list:
            logger.info(json.dumps(work_info))
            logger.info(f'爬取作品信息 https://www.douyin.com/video/{work_info["aweme_info"]["aweme_id"]}')
            work_info = handle_work_info(work_info['aweme_info'])
            work_info_list.append(work_info)
            if save_choice == 'all' or 'media' in save_choice:
                safe_download_work(work_info, base_path['media'], save_choice)
        if save_choice == 'all' or save_choice == 'excel':
            file_path = os.path.abspath(os.path.join(base_path['excel'], f'{excel_name}.xlsx'))
            save_to_xlsx(work_info_list, file_path)

if __name__ == '__main__':
    """
        此文件为爬虫的入口文件，可以直接运行
        dy_apis/douyin_apis.py 为爬虫的api文件，包含抖音的全部数据接口，可以继续封装
        dy_live/server.py 为监听抖音直播的入口文件，可以直接运行
        感谢star和follow
    """

    # ======================================================================
    # 使用说明：下面 4 个功能默认全部关闭（每行前面都有 "# "）。
    # 想用哪个，就把那一段代码行前面的 "# " 删掉（# 和它后面的一个空格都删）。
    #
    # 【缩进规则 —— 不遵守会报 IndentationError / SyntaxError】
    #   1. 取消注释后，每一行开头必须正好是 4 个空格，和下面的
    #      data_spider = Data_Spider() 这一行左边对齐。
    #   2. 只删 "# " 这两个字符，不要删前面的 4 个空格，也不要多删或多加空格。
    #   3. 只用空格，不要用 Tab 键缩进（Tab 和空格混用也会报错）。
    #   4. works = [ ... ] 这种跨多行的，里面那一行比上下多 4 个空格（共 8 个），保持原样即可。
    #   5. 带 "##" 开头的中文说明行不用动，留着当注释。
    #   6. 引号必须是英文引号 ' 或 "，不能是中文弯引号 ‘’ “”。
    #      用 Mac「文本编辑」改时，先在菜单「格式」里选「制作纯文本」，并在
    #      设置里关闭「智能引号」；更推荐用 VS Code / PyCharm。
    #   小技巧：VS Code / PyCharm 里选中多行，按 Cmd + / 可一键注释或取消注释，
    #   缩进会自动保持正确。
    #
    # save_choice（保存方式）:
    #   'all'   保存所有信息（视频图片 + excel）
    #   'media' 只下载视频和图片（'media-video' 只下视频，'media-image' 只下图片）
    #   'excel' 只保存到 excel
    # ======================================================================

    auth, base_path = init()
    data_spider = Data_Spider()

    ## 功能 1：爬取指定的几个作品（作品链接会过期，记得换成自己的）
    # works = [
    #     r'https://www.douyin.com/user/MS4wLjABAAAAv2Jr7Ngl7lQMjp4fw0AxtXkaHOgI_UL8aBJGGDSaU1g?from_tab_name=main&modal_id=7445533736877264178',
    # ]
    # data_spider.spider_some_work(auth, works, base_path, 'all', 'test')

    ## 功能 2：爬取某个用户的所有作品（用户链接换成自己的）
    # user_url = 'https://www.douyin.com/user/MS4wLjABAAAAULqT-SrJDT7RqeoxeGg1hB14Ia5UI9Pm66kzKmI1ITD2Fo3bUhqYePBaztkzj7U5?from_tab_name=main&relation=0&vid=7227654252435361061'
    # data_spider.spider_user_all_work(auth, user_url, base_path, 'all')

    ## 功能 3：搜索关键词的作品
    # query = "榴莲"
    # require_num = 20  # 搜索的数量
    # sort_type = '0'  # 排序方式 0 综合排序, 1 最多点赞, 2 最新发布
    # publish_time = '0'  # 发布时间 0 不限, 1 一天内, 7 一周内, 180 半年内
    # filter_duration = ""  # 视频时长 空字符串 不限, 0-1 一分钟内, 1-5 1-5分钟内, 5-10000 5分钟以上
    # search_range = "0"  # 搜索范围 0 不限, 1 最近看过, 2 还未看过, 3 关注的人
    # content_type = "0"  # 内容形式 0 不限, 1 视频, 2 图文
    # data_spider.spider_some_search_work(auth, query, require_num, base_path, 'all', sort_type, publish_time, filter_duration, search_range, content_type)

    ## 功能 4：给某个用户发私信（注意：会用你的账号真实发出消息！）
    # user_url = 'https://www.douyin.com/user/MS4wLjABAAAAaB23ankxsw7PIgXnKxCcLC9iJIadZMQQpS-KWVO8Y306zOksK9cUvT5QdoOIcsS6?from_tab_name=live'
    # content = "在吗"
    # to_user_id = DouyinAPI.get_user_info(auth, user_url)['user']['uid']
    # conversation_id, conversation_short_id, ticket = DouyinAPI.create_conversation(auth, to_user_id)
    # DouyinAPI.send_msg(auth, conversation_id, conversation_short_id, ticket, content)
