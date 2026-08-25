package demo.result;

public class WithdrawResultService {
    public String process(WithdrawApply apply) {
        return apply.getProjectNo();
    }

    public void validate(WithdrawApply apply) {
        if (apply.getProjectNo() == null) {
            throw new IllegalArgumentException();
        }
    }
}
