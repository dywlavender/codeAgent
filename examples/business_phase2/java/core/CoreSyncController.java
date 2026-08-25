package demo.core;

public class CoreSyncController {
    @PostMapping("/core/sync")
    public void sync(WithdrawApply apply) {
        coreClient.send(apply.getProjectNo());
    }
}
