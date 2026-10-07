# coding=utf-8
import json
import os
import sys
from loguru import logger

from dy_apis.douyin_api import DouyinAPI, parse_aweme_id
from utils.checkpoint import Checkpoint, make_key
from utils.common_util import init
from utils.data_util import handle_work_info, download_work, save_to_xlsx, handle_comment_info, save_comments_to_xlsx
from utils.safe_guard import RiskStop, DailyLimitReached, today_count


def safe_download_work(work_info, path, save_choice):
    # download_work 自带重试，重试后仍失败则跳过该作品，避免中断整批爬取；
    # 已下载过的文件会自动跳过（断点续爬）
    try:
        download_work(work_info, path, save_choice)
    except Exception as e:
        logger.error(f'作品 {work_info["work_id"]} 下载失败，已跳过: {e}')


def _excel_path(base_path, name):
    return os.path.abspath(os.path.join(base_path['excel'], f'{name}.xlsx'))


def _save_partial(save_fn, rows, base_path, name):
    """被迫中断时，把已经爬到的数据先存一份，文件名带"_未完成"。"""
    if rows:
        save_fn(rows, _excel_path(base_path, f'{name}_未完成'))


class Data_Spider():
    """
    所有爬取方法都带「断点续爬」：
      - 进度实时保存在 datas/checkpoints/，任务完成后自动删除；
      - 中途因风控 / 每日额度 / Ctrl+C / 断网停止时，已爬到的数据会先存成 "xxx_未完成.xlsx"；
      - 再次运行同样的代码，会从断点继续，已下载的视频图片也会自动跳过。
    请求限速、风控退避由 utils/safe_guard.py 在底层统一处理，这里不用管。
    """

    def __init__(self):
        self.douyin_apis = DouyinAPI()

    # ------------------------------------------------------------------
    # 作品
    # ------------------------------------------------------------------
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
        ckpt = Checkpoint('works', make_key(excel_name or 'works', *works))
        state = ckpt.load() or {'done': {}}
        state['failed'] = []  # 上次失败的，这次重新试一遍
        done = state['done']
        try:
            for i, work_url in enumerate(works, 1):
                if work_url in done or work_url in state['failed']:
                    continue
                try:
                    work_info = self.spider_work(auth, work_url)
                except RiskStop:
                    raise
                except Exception as e:
                    logger.error(f'作品 {work_url} 爬取失败，已跳过: {e}')
                    state['failed'].append(work_url)
                    ckpt.save(state)
                    continue
                if save_choice == 'all' or 'media' in save_choice:
                    safe_download_work(work_info, base_path['media'], save_choice)
                done[work_url] = work_info
                ckpt.save(state)
                logger.info(f'进度 {i}/{len(works)}')
        except (RiskStop, KeyboardInterrupt):
            if save_choice == 'all' or save_choice == 'excel':
                _save_partial(save_to_xlsx, [done[u] for u in works if u in done], base_path, excel_name)
            raise
        work_list = [done[u] for u in works if u in done]
        if save_choice == 'all' or save_choice == 'excel':
            save_to_xlsx(work_list, _excel_path(base_path, excel_name))
        ckpt.clear()
        return work_list

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
        sec_uid = user_url.split('/')[-1].split('?')[0]
        if save_choice == 'all' or save_choice == 'excel':
            excel_name = sec_uid
        ckpt = Checkpoint('user', make_key(sec_uid))
        state = ckpt.load() or {'cursor': '0', 'works': [], 'finished': False}
        work_info_list = state['works']
        try:
            user_info = self.douyin_apis.get_user_info(auth, user_url)
            while not state['finished']:
                res_json = self.douyin_apis.get_user_work_info(auth, user_url, state['cursor'])
                if 'aweme_list' not in res_json:
                    break
                for work in res_json['aweme_list'] or []:
                    work['author'].update(user_info['user'])
                    work_info = handle_work_info(work)
                    work_info_list.append(work_info)
                    logger.info(f'爬取作品信息 {work_info["work_url"]}')
                    if save_choice == 'all' or 'media' in save_choice:
                        safe_download_work(work_info, base_path['media'], save_choice)
                state['cursor'] = str(res_json.get('max_cursor', '0'))
                state['finished'] = res_json.get('has_more') != 1
                ckpt.save(state)
                logger.info(f'用户 {sec_uid}：已爬 {len(work_info_list)} 个作品')
        except (RiskStop, KeyboardInterrupt):
            if save_choice == 'all' or save_choice == 'excel':
                _save_partial(save_to_xlsx, work_info_list, base_path, excel_name)
            raise
        logger.info(f'用户 {user_url} 作品数量: {len(work_info_list)}')
        if save_choice == 'all' or save_choice == 'excel':
            save_to_xlsx(work_info_list, _excel_path(base_path, excel_name))
        ckpt.clear()
        return work_info_list

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
        if save_choice == 'all' or save_choice == 'excel':
            excel_name = query
        ckpt = Checkpoint('search', make_key(query, require_num, sort_type, publish_time,
                                             filter_duration, search_range, content_type))
        state = ckpt.load() or {'offset': '0', 'works': [], 'seen': [], 'finished': False}
        work_info_list = state['works']
        seen = set(state['seen'])
        try:
            while not state['finished'] and len(work_info_list) < require_num:
                res_json = self.douyin_apis.search_general_work(
                    auth, query, sort_type, publish_time, state['offset'],
                    filter_duration, search_range, content_type)
                data = res_json.get('data') or []
                for item in data:
                    aweme = item.get('aweme_info')
                    if not aweme or aweme.get('aweme_id') in seen or len(work_info_list) >= require_num:
                        continue
                    seen.add(aweme.get('aweme_id'))
                    logger.info(f'爬取作品信息 https://www.douyin.com/video/{aweme["aweme_id"]}')
                    work_info = handle_work_info(aweme)
                    work_info_list.append(work_info)
                    if save_choice == 'all' or 'media' in save_choice:
                        safe_download_work(work_info, base_path['media'], save_choice)
                state['offset'] = str(int(state['offset']) + len(data))
                state['finished'] = res_json.get('has_more') != 1 or not data
                state['seen'] = list(seen)
                ckpt.save(state)
                logger.info(f'搜索 {query}：已爬 {len(work_info_list)}/{require_num}')
        except (RiskStop, KeyboardInterrupt):
            if save_choice == 'all' or save_choice == 'excel':
                _save_partial(save_to_xlsx, work_info_list, base_path, excel_name)
            raise
        logger.info(f'搜索关键词 {query} 作品数量: {len(work_info_list)}')
        if save_choice == 'all' or save_choice == 'excel':
            save_to_xlsx(work_info_list, _excel_path(base_path, excel_name))
        ckpt.clear()
        return work_info_list

    # ------------------------------------------------------------------
    # 评论
    # ------------------------------------------------------------------
    def spider_work_comments(self, auth, work_url: str, base_path: dict, max_comments: int = 0,
                              with_reply: bool = True, max_reply_per_comment: int = 0,
                              excel_name: str = '', sleep_range=None):
        """
        爬取一个作品的评论（一级评论 + 楼中楼回复），保存到 excel
        :param auth: 用户认证信息
        :param work_url: 作品链接（/video/xxx、/note/xxx 或带 modal_id=xxx 的链接都行）
        :param base_path: 保存路径
        :param max_comments: 最多爬多少条一级评论，0 表示全部
        :param with_reply: 是否爬取二级回复（楼中楼）
        :param max_reply_per_comment: 每条一级评论最多爬多少条回复，0 表示全部
        :param excel_name: excel 文件名，留空则用 "评论_作品id"
        :param sleep_range: 已废弃，限速统一由 .env 里的防风控参数控制
        :return: 整理后的评论列表
        """
        aweme_id, _ = parse_aweme_id(work_url)
        excel_name = excel_name or f'评论_{aweme_id}'
        ckpt = Checkpoint('comments', make_key(aweme_id, max_comments, with_reply, max_reply_per_comment))
        state = ckpt.load() or {'cursor': '0', 'out_count': 0, 'rows': [], 'finished': False}
        rows = state['rows']
        try:
            while not state['finished']:
                res = self.douyin_apis.get_work_out_comment(auth, work_url, state['cursor'])
                comments = res.get('comments') or []
                page_rows = []
                for comment in comments:
                    if max_comments and state['out_count'] >= max_comments:
                        break
                    state['out_count'] += 1
                    page_rows.append(handle_comment_info(comment, level=1))
                    if with_reply and (comment.get('reply_comment_total') or 0) > 0:
                        page_rows.extend(self._spider_replies(auth, comment, max_reply_per_comment))
                # 一整页（含它的回复）都爬完才记进度，中断后重爬这一页，不会重复也不会漏
                rows.extend(page_rows)
                state['cursor'] = str(res.get('cursor', '0'))
                state['finished'] = (not comments or res.get('has_more') != 1
                                     or bool(max_comments and state['out_count'] >= max_comments))
                ckpt.save(state)
                logger.info(f'作品 {aweme_id}：已爬一级评论 {state["out_count"]} 条，共 {len(rows)} 条（含回复）')
        except (RiskStop, KeyboardInterrupt):
            _save_partial(save_comments_to_xlsx, rows, base_path, excel_name)
            raise
        save_comments_to_xlsx(rows, _excel_path(base_path, excel_name))
        ckpt.clear()
        return rows

    def _spider_replies(self, auth, comment: dict, max_reply: int):
        """爬一条一级评论下的二级回复；单条失败只跳过，不中断整体（风控停止除外）。"""
        replies = []
        cursor = '0'
        try:
            while True:
                res = self.douyin_apis.get_work_inner_comment(auth, comment, cursor, '10')
                for reply in res.get('comments') or []:
                    if max_reply and len(replies) >= max_reply:
                        return replies
                    replies.append(handle_comment_info(reply, level=2, parent_cid=comment.get('cid', '')))
                if res.get('has_more') != 1:
                    break
                cursor = str(res.get('cursor', '0'))
        except RiskStop:
            raise
        except Exception as e:
            logger.warning(f'评论 {comment.get("cid")} 的回复爬取失败，已跳过: {e}')
        return replies

    def spider_some_work_comments(self, auth, works: list, base_path: dict, max_comments: int = 0,
                                   with_reply: bool = True, max_reply_per_comment: int = 0):
        """
        批量爬取多个作品的评论，每个作品一个 excel。
        已完成的作品会记录下来，中断后再运行会跳过它们；正在爬的那个作品从断点继续。
        """
        ckpt = Checkpoint('comments_batch', make_key('batch', *works))
        state = ckpt.load() or {'done': []}
        state['failed'] = []  # 上次失败的，这次重新试一遍
        for i, work_url in enumerate(works, 1):
            if work_url in state['done'] or work_url in state['failed']:
                continue
            logger.info(f'===== 批量评论 {i}/{len(works)}：{work_url} =====')
            try:
                self.spider_work_comments(auth, work_url, base_path, max_comments,
                                          with_reply, max_reply_per_comment)
                state['done'].append(work_url)
            except (RiskStop, KeyboardInterrupt):
                raise
            except Exception as e:
                logger.error(f'作品 {work_url} 的评论爬取失败，已跳过: {e}')
                state['failed'].append(work_url)
            ckpt.save(state)
        if state['failed']:
            logger.warning(f'以下作品失败已跳过：{state["failed"]}')
        ckpt.clear()


def _friendly_exit(exc_type, exc, tb):
    """风控停止 / 额度用完 / 手动中断时，给出清楚的提示，而不是一大段报错。"""
    if issubclass(exc_type, DailyLimitReached):
        logger.warning(f'\n⏸  {exc}')
    elif issubclass(exc_type, RiskStop):
        logger.error(f'\n⛔ {exc}')
    elif issubclass(exc_type, KeyboardInterrupt):
        logger.warning('\n⏸  已手动停止。进度已保存，下次运行会从断点继续。')
    else:
        sys.__excepthook__(exc_type, exc, tb)
        return
    logger.info(f'今日已请求 {today_count()} 次。已爬到的数据保存在 datas/excel_datas/（文件名带"_未完成"）。')


sys.excepthook = _friendly_exit

if __name__ == '__main__':
    """
        此文件为爬虫的入口文件，可以直接运行
        dy_apis/douyin_apis.py 为爬虫的api文件，包含抖音的全部数据接口，可以继续封装
        dy_live/server.py 为监听抖音直播的入口文件，可以直接运行
        感谢star和follow
    """

    # ======================================================================
    # 使用说明：下面 6 个功能默认全部关闭（每行前面都有 "# "）。
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
    # 【防风控 & 断点续爬 —— 已自动开启，不用改代码】
    #   - 所有请求自动限速（默认每次间隔 3~6 秒、每分钟 ≤10 次、每天 ≤2000 次），
    #     触发风控会自动暂停 5/10/20 分钟后重试，仍不行就保存进度后安全停止。
    #   - 中途停止（风控、额度用完、Ctrl+C、断网）后，再次运行同样的代码会从断点继续。
    #   - 速度参数在 .env 里调整（DY_REQ_INTERVAL、DY_REQ_PER_MIN 等），见 .env.example。
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

    ## 功能 5：爬取某个作品的评论（含楼中楼回复），保存到 datas/excel_datas/评论_作品id.xlsx
    # work_url = 'https://www.douyin.com/video/7445533736877264178'
    # data_spider.spider_work_comments(auth, work_url, base_path,
    #                                  max_comments=200,          # 最多爬多少条一级评论，0 = 全部
    #                                  with_reply=True,           # 是否爬楼中楼回复
    #                                  max_reply_per_comment=50)  # 每条评论最多爬多少回复，0 = 全部

    ## 功能 6：批量爬取多个作品的评论（每个作品一个 excel；中断后再运行会跳过已完成的）
    # works = [
    #     'https://www.douyin.com/video/7445533736877264178',
    #     'https://www.douyin.com/video/7227654252435361061',
    # ]
    # data_spider.spider_some_work_comments(auth, works, base_path,
    #                                       max_comments=200, with_reply=True, max_reply_per_comment=50)
