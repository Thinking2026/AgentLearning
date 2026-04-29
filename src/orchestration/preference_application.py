from __future__ import annotations


class UserPreferenceApplicationService:
    """跨上下文应用服务骨架：向执行层注入用户偏好。"""

    def get_preferences(self) -> dict[str, str]:
        return {}  # 骨架：偏好注入留作后续实现
