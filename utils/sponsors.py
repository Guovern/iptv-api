from utils.i18n import t


HELODATA_README_URL = "https://helodata.com?ref=iptvapi1"
HELODATA_APP_URL = "https://helodata.com?ref=iptvapi2"


def helodata_console_message() -> str:
    coupon = t("msg.helodata_coupon")
    promotion = t("msg.helodata_promotion").format(
        coupon=coupon,
        url=HELODATA_APP_URL,
    )
    return promotion
