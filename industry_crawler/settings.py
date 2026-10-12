"""Settings for crawling manually supplied job boards responsibly."""

BOT_NAME = "industry_crawler"
SPIDER_MODULES = ["industry_crawler.spiders"]
NEWSPIDER_MODULE = "industry_crawler.spiders"

ROBOTSTXT_OBEY = True
CONCURRENT_REQUESTS = 8
CONCURRENT_REQUESTS_PER_DOMAIN = 1
DOWNLOAD_DELAY = 1
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1
AUTOTHROTTLE_MAX_DELAY = 60
DOWNLOAD_TIMEOUT = 30
RETRY_TIMES = 2
FEED_EXPORT_ENCODING = "utf-8"
CS_RELEVANCE_THRESHOLD = 0.6
JOB_OUTPUT_DIR = "data"
USER_AGENT = "TeachTrackII research crawler"
TELNETCONSOLE_ENABLED = False
REMOTE_CONTROL_ENABLED = False
DOWNLOADER_MIDDLEWARES = {"industry_crawler.retry.BackoffMiddleware": 550}
