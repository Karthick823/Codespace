| Test ID | Scenario                                  | Expected result                    | Actual result |

| ------- | ----------------------------------------- | ---------------------------------- | ------------- |

| AUTH-01 | Valid login                               | Login succeeds                     | Completed     |

| AUTH-02 | Incorrect password                        | Login denied                       | Completed	   |

| AUTH-03 | Inactive account                          | Login denied                       | Completed	   |

| AUTH-04 | Locked account                            | Login denied                       | Completed	   |

| AUTH-05 | Logout followed by protected operation    | Access denied                      | Completed	   |

| AUTH-06 | Invalid role in session                   | Access denied                      | Completed	   |

| AUTH-07 | Disabled account with an existing session | Access revoked according to policy | Completed	   |



