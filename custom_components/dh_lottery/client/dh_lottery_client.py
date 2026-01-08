import datetime
import logging
import threading
from dataclasses import dataclass
from typing import Any

import aiohttp

from .dh_rsa import RSAKey

_LOGGER = logging.getLogger(__name__)

DH_LOTTERY_URL = "https://www.dhlottery.co.kr"

@dataclass
class DhLotteryBalanceData:
    deposit: int = 0  # 총예치금
    purchase_available: int = 0  # 구매가능금액
    reservation_purchase: int = 0  # 예약구매금액
    withdrawal_request: int = 0  # 출금신청중금액
    purchase_impossible: int = 0  # 구매불가능금액
    this_month_accumulated_purchase: int = 0  # 이번달누적구매금액


class DhLotteryError(Exception):
    """DH Lottery 예외 클래스입니다."""

class DhAPIError(DhLotteryError):
    """DH API 예외 클래스입니다."""

class DhLotteryLoginError(DhLotteryError):
    """로그인에 실패했을 때 발생하는 예외입니다."""


class DhLotteryClient:
    """동행복권 클라이언트 클래스입니다."""

    def __init__(self, username: str, password: str):
        """DhLotteryClient 클래스를 초기화합니다."""
        self.username = username
        self._password = password
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=False),
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/91.0.4472.77 Safari/537.36",
                "Connection": "keep-alive",
                "Cache-Control": "max-age=0",
                "sec-ch-ua": '" Not;A Brand";v="99", "Google Chrome";v="91", "Chromium";v="91"',
                "sec-ch-ua-mobile": "?0",
                "Upgrade-Insecure-Requests": "1",
                "Origin": DH_LOTTERY_URL,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,"
                "*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
                "Referer": f"{DH_LOTTERY_URL}/login",
                "Sec-Fetch-Site": "same-site",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-User": "?1",
                "Sec-Fetch-Dest": "document",
                "Accept-Language": "ko,en-US;q=0.9,en;q=0.8,ko-KR;q=0.7",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        self._rsa_key = RSAKey()
        self.lock = threading.RLock()
        self.logged_in = False

    @staticmethod
    async def handle_response_json(response) -> dict[str, Any]:
        result = await response.json()
        if response.status != 200 or response.reason != 'OK':
            raise DhAPIError('❗ API 요청에 실패했습니다.')

        if 'data' not in result:
            raise DhLotteryError('❗ API 응답 데이터가 올바르지 않습니다.')
        return result.get('data', {})

    async def async_get(self, path: str, params: dict) -> dict:
        """로그인이 필요하지 않은 페이지를 가져옵니다."""
        try:
            resp = await self.session.get(url=f"{DH_LOTTERY_URL}/{path}", params=params)
            return await self.handle_response_json(resp)
        except DhLotteryError as ex:
            raise ex
        except Exception as ex:
            raise DhLotteryError(
                "❗페이지를 가져오지 못했습니다."
            ) from ex

    async def async_get_with_login(
        self,
        path: str,
        params: dict,
        retry: int = 1,
    ) -> dict[str, Any]:
        """로그인이 필요한 페이지를 가져옵니다."""
        with self.lock:
            try:
                return await self.async_get(path, params)
            except DhAPIError:
                if retry > 0:
                    await self.async_login()
                    return await self.async_get_with_login(path, params, retry - 1)
                raise DhLotteryLoginError("❗로그인 또는 API 요청에 실패했습니다.")
            except DhLotteryError:
                raise
            except Exception as ex:
                raise DhLotteryError(
                    "❗로그인이 필요한 페이지를 가져오지 못했습니다."
                ) from ex

    async def async_login(self):
        """로그인을 수행합니다."""
        _LOGGER.info("로그인 시작")
        try:
            await self._async_set_select_rsa_module()
            # 로그인 POST 요청
            resp = await self.session.post(
                url=f"{DH_LOTTERY_URL}/login/securityLoginCheck.do",
                data={
                    "userId": self._rsa_key.encrypt(self.username),
                    "userPswdEncn": self._rsa_key.encrypt(self._password),
                    "inpUserId": self.username,
                },
            )
            if resp.status != 200 or resp.reason != 'OK' or 'loginSuccess.do' not in str(resp.request_info.url):
                self.logged_in = False
                raise DhLotteryLoginError(
                    "로그인에 실패했습니다. 아이디 또는 비밀번호를 확인해주세요. (5회 실패했을 수도 있습니다. 이 경우엔 홈페이지에서 비밀번호를 변경해야 합니다)"
                )
            self.logged_in = True
        except DhLotteryError:
            raise
        except Exception as ex:
            raise DhLotteryError("❗로그인을 수행하지 못했습니다.") from ex

    async def _async_set_select_rsa_module(self) -> None:
        """RSA 공개키를 설정합니다."""
        resp = await self.session.get(
            url=f"{DH_LOTTERY_URL}/login/selectRsaModulus.do",
        )
        result = await resp.json()
        data = result.get("data")
        self._rsa_key.set_public(
            data.get("rsaModulus"), data.get("publicExponent")
        )

    async def async_get_balance(self) -> DhLotteryBalanceData:
        """예치금 현황을 조회합니다."""
        try:
            current_time = int(datetime.datetime.now().timestamp() * 1000)
            user_result = await self.async_get_with_login("mypage/selectUserMndp.do", params={"_": current_time},)

            user_mndp = user_result.get("userMndp", {})
            pnt_dpst_amt = user_mndp.get("pntDpstAmt", 0)
            pnt_tkmny_amt = user_mndp.get("pntTkmnyAmt", 0)
            ncsbl_dpst_Amt = user_mndp.get("ncsblDpstAmt", 0)
            ncsbl_tkmny_amt = user_mndp.get("ncsblTkmnyAmt", 0)
            csbl_dpst_amt = user_mndp.get("csblDpstAmt", 0)
            csbl_tkmny_amt = user_mndp.get("csblTkmnyAmt", 0)
            total_amt = (pnt_dpst_amt - pnt_tkmny_amt) + (ncsbl_dpst_Amt - ncsbl_tkmny_amt) + (csbl_dpst_amt - csbl_tkmny_amt)

            crnt_entrs_amt = user_mndp.get("crntEntrsAmt", 0)
            rsvt_ordr_amt = user_mndp.get("rsvtOrdrAmt", 0)
            daw_aply_amt = user_mndp.get("dawAplyAmt", 0)
            fee_amt = user_mndp.get("feeAmt", 0)

            purchase_impossible = rsvt_ordr_amt + daw_aply_amt + fee_amt

            home_result = await self.async_get_with_login(
                "mypage/selectMyHomeInfo.do",
                params={"_": current_time},
            )
            prchs_lmt_info = home_result.get("prchsLmtInfo", {})
            wly_prchs_acml_amt = prchs_lmt_info.get("wlyPrchsAcmlAmt", 0)
            return DhLotteryBalanceData(
                deposit = total_amt,
                purchase_available=crnt_entrs_amt,
                reservation_purchase=rsvt_ordr_amt,
                withdrawal_request=daw_aply_amt,
                purchase_impossible=purchase_impossible,
                this_month_accumulated_purchase=wly_prchs_acml_amt,
            )
        except Exception as ex:
            raise DhLotteryError("❗예치금 현황을 조회하지 못했습니다.") from ex

    async def async_get_buy_list(self, lotto_id: str) -> list[dict[str, Any]]:
        """1주일간의 구매내역을 조회합니다."""
        end_date = datetime.datetime.now()
        start_date = end_date - datetime.timedelta(days=7)
        try:
            result = await self.async_get_with_login(
                "mypage/selectMyLotteryledger.do",
                params={
                    "srchStrDt": start_date.strftime("%Y%m%d"),
                    "srchEndDt": end_date.strftime("%Y%m%d"),
                    "ltGdsCd": lotto_id,
                    "pageNum": 1,
                    "recordCountPerPage": 1000,
                    "_": int(datetime.datetime.now().timestamp() * 1000)
                },
            )
            return result.get("list", [])
        except Exception as ex:
            raise DhLotteryError(
                "❗최근 1주일간의 구매내역을 조회하지 못했습니다."
            ) from ex

    async def async_get_accumulated_prize(self, lotto_id: str) -> int:
        """지급기한이 종료되지 않은 당첨금 누적금액을 조회합니다. 기간 1년"""
        end_date = datetime.datetime.now()
        start_date = end_date - datetime.timedelta(days=365)
        try:
            result = await self.async_get_with_login(
                "mypage/selectMyLotteryledger.do",
                params={
                    "srchStrDt": start_date.strftime("%Y%m%d"),
                    "srchEndDt": end_date.strftime("%Y%m%d"),
                    "ltGdsCd": lotto_id,
                    "pageNum": 1,
                    "winResult": "T",
                    "recordCountPerPage": 1000,
                    "_": int(datetime.datetime.now().timestamp() * 1000),
                },
            )
            items = result.get("list", [])

            accum_prize: int = 0
            for item in items:
                accum_prize += item.get("ltWnAmt", 0)
            return accum_prize

        except Exception as ex:
            raise DhLotteryError(
                "❗지급기한이 종료되지 않은 당첨금을 조회하지 못하였습니다.."
            ) from ex
