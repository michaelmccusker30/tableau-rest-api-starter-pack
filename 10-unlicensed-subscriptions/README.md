# 10 · Unlicensed subscriptions: back up, then delete

When someone loses their licence, their subscriptions stay behind: scheduled emails that can't be delivered. This finds the subscriptions that belong to **Unlicensed** users, shows them to you, backs every one up to CSV, and deletes them.

This entry **deletes from your site**, and deleted subscriptions can't be restored through the API. Run it with `--dry-run` first.

```bash
python 10-unlicensed-subscriptions/10_unlicensed_subscriptions.py --dry-run
python 10-unlicensed-subscriptions/10_unlicensed_subscriptions.py
```


## The flow

1. **List** every subscription on the site.
2. **Check** the site role of each user who owns one, and only those users.
3. **Review:** each unlicensed user, their subscriptions (subject, content, schedule), the total to delete, and where the backup will go.
4. **Confirm** (the default is No). The backup CSV is written, then the subscriptions are deleted one at a time, and the backup is updated with each outcome.

```text
Review
  Site           acme on https://10ax.online.tableau.com
  Subscriptions  214 from 97 users
  To delete      9, owned by 3 unlicensed users
  Backup         output/unlicensed_subscriptions_acme_2026-10-10_1432.csv

  Sam Gone  5 subscriptions
      Budget daily  Budget · Weekday mornings
      ...
? Back up and delete 9 subscriptions? (y/N):
```

| Flag | Effect |
| --- | --- |
| `--output-dir PATH` | Folder for the backup (default: `./output`) |
| `-y`, `--yes` | No prompts: back up, then delete |
| `--dry-run` | The review only: nothing is deleted and no file is written |
| `--env-file PATH` | Use a different settings file |

## The backup

`unlicensed_subscriptions_<site>_<date>.csv`, one row per subscription: Subject, User, Username, Content Type, Content (the workbook, or workbook / view), Schedule, Attach Image, Attach PDF, Suspended, Message, Status (`Deleted` or `Failed`), Detail, and the subscription, user, content and schedule IDs.

It's written **before the first delete**. It holds what you'd need to recreate a subscription by hand if one was removed by mistake: who, what, when, and in what format.

## Safeguards

| Situation | What happens |
| --- | --- |
| Someone hasn't signed in for months but is still licensed | Their subscriptions stay. Only `Unlicensed` counts |
| A subscriber can't be looked up (deleted, say) | Reported and left alone, not treated as unlicensed |
| You press Enter at the confirmation | Cancelled: the default is No |
| One delete fails | Recorded in the backup; the rest carry on. Exit code 1 |

## REST calls it makes

| Step | Call |
| --- | --- |
| Subscriptions | `GET /sites/{site-id}/subscriptions` (all pages) |
| Each subscriber's licence | `GET /sites/{site-id}/users/{id}`, once per user who owns a subscription |
| Names for the backup | `GET /workbooks`, `/views` (all pages), only if something will be deleted |
| Delete | `DELETE /sites/{site-id}/subscriptions/{id}` |

**Fewer calls, the original's idea.** On a site with 20,000 users and 150 subscribers, looking up only the subscribers is 150 small calls instead of paging through every user. On a small site, listing every user could be fewer calls. The original chose the approach that scales with what you're cleaning up, and so does this.
