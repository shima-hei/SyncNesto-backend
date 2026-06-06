"""アプリケーション共通のエラーメッセージを定義するモジュール。"""

APPLICATION_ERROR = "Application error"
BAD_REQUEST = "Bad request"
UNAUTHORIZED = "Unauthorized"
AUTHENTICATION_REQUIRED = "Authentication required"
TOKEN_EXPIRED = "Token expired"
INVALID_TOKEN = "Invalid token"
INVALID_CSRF_TOKEN = "Invalid CSRF token"
FORBIDDEN = "Forbidden"
NOT_FOUND = "Not found"
CONFLICT = "Conflict"
DUPLICATE_RESOURCE = "Resource already exists"
VERSION_CONFLICT = "Resource version conflict"
INVALID_CREDENTIALS = "Invalid email or password"
EMAIL_ALREADY_REGISTERED = "Email already registered"

INVALID_SYSTEM_ROLE_KEY = "Invalid system role key: {role_key}"

UNSUPPORTED_IMAGE_CONTENT_TYPE = "Unsupported image content type"
IMAGE_FILE_REQUIRED = "Image file is required"
IMAGE_FILE_TOO_LARGE = "Image file is too large"

PROJECT_CODE_ALREADY_EXISTS = "Project code already exists"
PROJECT_NOT_FOUND = "Project not found"
PROJECT_MEMBER_ALREADY_EXISTS = "Project member already exists"
PROJECT_MEMBER_NOT_FOUND = "Project member not found"
PROJECT_ROLE_NOT_FOUND = "Project role not found"

USER_NOT_FOUND = "User not found"

REQUIREMENT_DOCUMENT_CODE_ALREADY_EXISTS = (
    "Requirement document code already exists"
)
REQUIREMENT_DOCUMENT_NOT_FOUND = "Requirement document not found"
REQUIREMENT_CODE_ALREADY_EXISTS = "Requirement code already exists"
REQUIREMENT_NOT_FOUND = "Requirement not found"
REQUIREMENT_DETAIL_NOT_FOUND = "Requirement detail not found"
REQUIREMENT_LINK_NOT_FOUND = "Requirement link not found"
REQUIREMENT_COMMENT_NOT_FOUND = "Requirement comment not found"
REQUIREMENT_REVIEW_NOT_FOUND = "Requirement review not found"
