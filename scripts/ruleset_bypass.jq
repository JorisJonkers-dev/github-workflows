# Merge bypass actors into a ruleset body. Input: the desired ruleset (template).
# Args: $cur = the repo's existing ruleset (object, or null when none),
#       $allow = 1 when removing existing bypass actors is permitted.
# Default: keep every existing actor, add template actors that are missing,
# never remove (an actor's identity is actor_type + actor_id; the existing
# bypass_mode wins). With $allow == 1 the template's list is used as-is.
def same($a; $b): $a.actor_type == $b.actor_type and $a.actor_id == $b.actor_id;
(($cur // {}).bypass_actors // []) as $have
| (.bypass_actors // []) as $tpl
| if $allow == 1 then .bypass_actors = $tpl
  else .bypass_actors = ($have + [$tpl[] | . as $t | select(($have | map(select(same(.; $t))) | length) == 0)])
  end
