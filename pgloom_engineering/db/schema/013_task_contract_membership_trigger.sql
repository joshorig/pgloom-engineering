create or replace function engineering_task_contract_membership_sync()
returns trigger
language plpgsql
as $$
begin
  new.milestone_id = coalesce(
    new.milestone_id,
    new.input_contract->>'milestone_id',
    new.input_contract->'inputs'->>'milestone_id'
  );
  new.task_slice_id = coalesce(
    new.task_slice_id,
    new.input_contract->>'task_slice_id',
    new.input_contract->'inputs'->>'task_slice_id'
  );
  return new;
end;
$$;

drop trigger if exists engineering_task_contract_membership_sync_trigger
  on engineering_task_contracts;

create trigger engineering_task_contract_membership_sync_trigger
before insert or update on engineering_task_contracts
for each row
execute function engineering_task_contract_membership_sync();

update engineering_task_contracts
set
  milestone_id = coalesce(
    milestone_id,
    input_contract->>'milestone_id',
    input_contract->'inputs'->>'milestone_id'
  ),
  task_slice_id = coalesce(
    task_slice_id,
    input_contract->>'task_slice_id',
    input_contract->'inputs'->>'task_slice_id'
  )
where milestone_id is null
   or task_slice_id is null;
