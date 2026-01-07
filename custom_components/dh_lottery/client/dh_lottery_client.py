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
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/143.0.0.0 Safari/537.36",
                "Connection": "keep-alive",
                "Cache-Control": "max-age=0",
                "sec-ch-ua": '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "Upgrade-Insecure-Requests": "1",
                "Origin": DH_LOTTERY_URL,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,"
                "*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Referer": f"{DH_LOTTERY_URL}/login",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-User": "?1",
                "Sec-Fetch-Dest": "document",
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                "DNT": "1",
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
                allow_redirects=True,  # 리다이렉트 자동 처리
            )
            
            # 로그인 성공 확인
            # 1. 최종 URL에 loginSuccess.do가 포함되어 있는지 확인
            final_url = str(resp.url)
            _LOGGER.info(f"로그인 후 최종 URL: {final_url}")
            _LOGGER.info(f"응답 상태: {resp.status} {resp.reason}")
            
            # 2. 리다이렉트 히스토리 확인 (선택사항)
            if resp.history:
                _LOGGER.info(f"리다이렉트 발생: {len(resp.history)}회")
                for i, redirect_resp in enumerate(resp.history):
                    _LOGGER.info(f"  {i+1}. {redirect_resp.status} -> {redirect_resp.url}")
            
            # 3. 성공 조건: 200 OK이고 URL에 loginSuccess.do 포함
            if resp.status == 200 and 'loginSuccess.do' in final_url:
                self.logged_in = True
                _LOGGER.info("로그인 성공!")
                return
            
            # 4. 실패 처리
            _LOGGER.error(f"로그인 실패 - Status: {resp.status}, URL: {final_url}")
            self.logged_in = False
            
            # 응답 내용 확인 (디버깅용)
            try:
                response_text = await resp.text()
                if "실패" in response_text or "오류" in response_text:
                    _LOGGER.error(f"응답 내용에 오류 메시지 포함: {response_text[:200]}")
            except:
                pass
            
            raise DhLotteryLoginError(
                "로그인에 실패했습니다. 아이디 또는 비밀번호를 확인해주세요. "
                "(5회 실패했을 수도 있습니다. 이 경우엔 홈페이지에서 비밀번호를 변경해야 합니다)"
            )
            
        except DhLotteryError:
            raise
        except Exception as ex:
            _LOGGER.exception("로그인 중 예외 발생")
            raise DhLotteryError("❗로그인을 수행하지 못했습니다.") from ex

    async def _async_set_select_rsa_module(self) -> None:
        """RSA 모듈을 설정합니다. API 우선, 실패 시 로그인 페이지에서 파싱"""
        try:
            # 먼저 API 엔드포인트 시도
            resp = await self.session.get(
                url=f"{DH_LOTTERY_URL}/login/selectRsaModulus.do",
            )
            result = await resp.json()
            data = result.get("data")
            if data and data.get("rsaModulus") and data.get("publicExponent"):
                self._rsa_key.set_public(
                    data.get("rsaModulus"), data.get("publicExponent")
                )
                _LOGGER.info("RSA 키를 API에서 가져왔습니다.")
                return
        except Exception as e:
            _LOGGER.warning(f"API에서 RSA 키 가져오기 실패: {e}, 로그인 페이지에서 파싱 시도")
        
        # API 실패 시 로그인 페이지에서 RSA 키 파싱
        try:
            import re
            resp = await self.session.get(url=f"{DH_LOTTERY_URL}/login")
            html = await resp.text()
            
            # HTML에서 rsaModulus와 publicExponent 추출
            modulus_match = re.search(r"var\s+rsaModulus\s*=\s*'([a-fA-F0-9]+)'", html)
            exponent_match = re.search(r"var\s+publicExponent\s*=\s*'([a-fA-F0-9]+)'", html)
            
            if modulus_match and exponent_match:
                self._rsa_key.set_public(
                    modulus_match.group(1),
                    exponent_match.group(1)
                )
                _LOGGER.info("RSA 키를 로그인 페이지에서 파싱했습니다.")
                return
            else:
                raise DhLotteryError("로그인 페이지에서 RSA 키를 찾을 수 없습니다.")
        except Exception as ex:
            raise DhLotteryError(f"RSA 키를 가져오지 못했습니다: {ex}") from ex

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
