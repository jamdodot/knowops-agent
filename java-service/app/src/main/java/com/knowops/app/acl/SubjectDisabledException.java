package com.knowops.app.acl;

/**
 * 账号非 active（一票否决，ADR-0001）。
 *
 * <p>对应契约错误码 {@code SUBJECT_DISABLED}（HTTP 403）。
 */
public class SubjectDisabledException extends RuntimeException {

    private final String userId;
    private final String accountStatus;

    public SubjectDisabledException(String userId, String accountStatus) {
        super("account_status=" + accountStatus + " for user " + userId);
        this.userId = userId;
        this.accountStatus = accountStatus;
    }

    public String getUserId() {
        return userId;
    }

    public String getAccountStatus() {
        return accountStatus;
    }
}
