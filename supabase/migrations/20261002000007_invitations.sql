-- Auto-claim invitations on sign-in.
-- The desktop app no longer manages organizations; admins invite from the web
-- console and the invited user's client calls this RPC once after sign-in.

create or replace function public.claim_invitations()
returns setof uuid
language plpgsql
security definer
set search_path = public
as $$
declare
    caller_email text;
    invite record;
begin
    if auth.uid() is null then
        raise exception 'Not authenticated';
    end if;

    select email into caller_email from auth.users where id = auth.uid();

    for invite in
        select * from public.invitations
        where accepted_at is null
          and expires_at > now()
          and lower(email) = lower(caller_email)
        for update
    loop
        insert into public.memberships (org_id, user_id, role)
        values (invite.org_id, auth.uid(), invite.role)
        on conflict (org_id, user_id) do update set role = excluded.role;

        update public.invitations
        set accepted_at = now()
        where id = invite.id;

        insert into public.audit_log (org_id, actor, action, target)
        values (invite.org_id, auth.uid(), 'invitation.claimed', invite.id::text);

        return next invite.org_id;
    end loop;
    return;
end;
$$;

grant execute on function public.claim_invitations() to authenticated;
